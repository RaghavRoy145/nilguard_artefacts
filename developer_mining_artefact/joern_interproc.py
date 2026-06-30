#!/usr/bin/env python3
"""
joern_interproc.py -- Inter-procedural extension via Joern CPG queries.

DESIGN: Semgrep is the source of truth for which locations have critical
writes near dereferences. Joern ONLY extends those locations cross-
procedurally. It does NOT independently scan for dereferences.

This avoids the inflation problem (303K flows) from the previous version.

TWO QUERIES:
  IP1: From semgrep C3 (return-feeding) matches, check if callers use
       the return value in a conditional.
  IP2: From semgrep C6 (out-param) matches, check if callers use the
       mutated argument in a conditional.

WHY ONLY THESE TWO: Heap writes (C1) and call-escaping (C4) are self-
contained as critical; they don't need cross-function tracking. The
semgrep overlap matrix handles intra-procedural intersections. IP1/IP2
are the only flows where data crosses a function boundary invisibly.
"""

import argparse, json, os, re, subprocess, sys, csv
from pathlib import Path
from collections import defaultdict

RETURN_FEEDING_IDS = {
    "C3a-write-feeds-return-after-arrow-deref",
    "C3b-write-feeds-return-after-star-deref",
}
OUTPARAM_IDS = {"C6-output-param-write-near-deref"}

JOERN_MAX_MEMORY = "8g"
CPG_TIMEOUT = 3600
QUERY_TIMEOUT = 1200
MAX_C_FILES = 15000


def extract_target_functions(semgrep_raw_path):
    """Extract (file, line) from semgrep C3/C6 matches."""
    if not os.path.exists(semgrep_raw_path):
        return {"return_feeding": {}, "outparam": {}}
    with open(semgrep_raw_path) as f:
        data = json.load(f)

    ret_funcs = defaultdict(list)
    out_funcs = defaultdict(list)

    for finding in data.get("results", []):
        rule_id = finding.get("check_id", "").split(".")[-1]
        path = finding.get("path", "")
        line = finding.get("start", {}).get("line", 0)
        if rule_id in RETURN_FEEDING_IDS:
            ret_funcs[path].append(line)
        elif rule_id in OUTPARAM_IDS:
            out_funcs[path].append(line)

    return {"return_feeding": dict(ret_funcs), "outparam": dict(out_funcs)}


def _build_target_set_scala(targets):
    """Build Scala Set of (basename, line) from targets dict."""
    entries = []
    for path, lines in targets.items():
        basename = os.path.basename(path)
        for l in sorted(set(lines)):
            entries.append(f'("{basename}", {l})')
    return "Set(" + ", ".join(entries[:5000]) + ")"


def _build_func_finder_scala(targets_scala):
    """Scala code to find enclosing functions for target locations.

    Optimized: filters by filename FIRST (Joern indexes filenames),
    then checks line numbers only for methods in matching files.
    This avoids scanning all methods in large CPGs like CPython.
    """
    return f"""
val targetLocs = {targets_scala}

// Group target lines by filename suffix for efficient lookup
val targetByFile = targetLocs.groupBy(_._1).map {{ case (f, pairs) =>
  (f, pairs.map(_._2).toSet)
}}

// Step 1: Collect target filenames (basename suffixes)
val targetFileSuffixes = targetByFile.keys.toSeq

// Step 2: For each target file, find methods in that file and check lines
val targetFuncNames = scala.collection.mutable.Set[String]()

for ((fileSuffix, targetLines) <- targetByFile) {{
  val methodsInFile = cpg.method
    .nameNot("<global>|<operator>.*|<init>|<clinit>")
    .where(_.filename(".*" + java.util.regex.Pattern.quote(fileSuffix)))
    .l

  for (m <- methodsInFile) {{
    val mLines = m.ast.lineNumber.toSet
    if (targetLines.exists(l => mLines.contains(l))) {{
      targetFuncNames += m.name
    }}
  }}
}}

System.err.println("DIAG: targetFuncNames.size=" + targetFuncNames.size)
"""


def build_resolve_functions_query(targets):
    """Resolve (file, line) pairs to function names via a lightweight query.
    Returns JSON array of function names."""
    if not targets:
        return 'println("###JOERN_JSON_START###\\n[]\\n###JOERN_JSON_END###")'

    targets_scala = _build_target_set_scala(targets)
    func_finder = _build_func_finder_scala(targets_scala)

    return f"""
import io.shiftleft.semanticcpg.language._

{func_finder}

println("###JOERN_JSON_START###")
println("[" + targetFuncNames.map(n => s"\\"$n\\"").mkString(",") + "]")
println("###JOERN_JSON_END###")
"""


def build_ip1_query_from_names(func_names):
    """IP1 query using bulk traversal (no per-function loop).

    Instead of iterating per function, we:
    1. Find ALL assignments where RHS calls any target function (one pass).
    2. For each, check if the LHS variable appears in a conditional (one pass per hit).

    This avoids the O(n_functions * n_call_sites) loop that kills Joern on large CPGs.
    """
    if not func_names:
        return 'println("###JOERN_JSON_START###\\n[]\\n###JOERN_JSON_END###")'

    names_scala = "Set(" + ", ".join(f'"{n}"' for n in func_names) + ")"

    return f"""
import io.shiftleft.semanticcpg.language._

val targetFuncNames = {names_scala}
val results = scala.collection.mutable.ListBuffer[String]()

System.err.println("DIAG: IP1 checking " + targetFuncNames.size + " functions (bulk)")

// Case A: Direct use — if (func(...)) {{ ... }}
val directHits = cpg.call.filter(c => targetFuncNames.contains(c.name)).l
  .flatMap {{ cs =>
    val callerMethod = cs.method
    callerMethod.ast.isControlStructure
      .where(_.condition.ast.isCall.filter(c => c.name == cs.name))
      .l
      .map(cond => (cs, callerMethod, cond))
  }}

System.err.println("DIAG: Case A direct hits: " + directHits.size)

for ((cs, callerMethod, cond) <- directHits) {{
  results += s\"\"\"{{\"callee\":\"${{cs.name}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{cs.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"type\":\"IP1\"}}\"\"\"
}}

// Case B: Assigned then checked — ret = func(...); if (ret ...) {{ ... }}
// Step B1: Find all assignments whose RHS contains a call to a target function
val targetAssigns = cpg.call.name("<operator>.assignment")
  .where(_.argument(2).ast.isCall.filter(c => targetFuncNames.contains(c.name)))
  .l

System.err.println("DIAG: Case B target assignments: " + targetAssigns.size)

for (asgn <- targetAssigns) {{
  try {{
    val ident = asgn.argument(1).code
    val callerMethod = asgn.method
    val funcName = asgn.argument(2).ast.isCall
      .filter(c => targetFuncNames.contains(c.name))
      .name.headOption.getOrElse("?")

    // Step B2: Check if ident appears in any conditional in the same method
    val condUses = callerMethod.ast.isControlStructure
      .where(_.condition.ast.isIdentifier.name(ident))
      .l

    for (cond <- condUses) {{
      results += s\"\"\"{{\"callee\":\"${{funcName}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{asgn.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"variable\":\"${{ident}}\",\"type\":\"IP1\"}}\"\"\"
    }}
  }} catch {{
    case e: Exception =>
      System.err.println("DIAG: SKIP assignment at line " + asgn.lineNumber.getOrElse(-1) + ": " + e.getMessage)
  }}
}}

System.err.println("DIAG: IP1 total results=" + results.size)
println("###JOERN_JSON_START###")
println("[" + results.mkString(",\\n") + "]")
println("###JOERN_JSON_END###")
"""


def build_ip1_query(targets):
    """IP1: return-feeding write near deref -> caller uses return in conditional.
    LEGACY wrapper — kept for compatibility, delegates to name-based query."""
    # This is now only used if we don't do the two-step approach
    if not targets:
        return 'println("###JOERN_JSON_START###\\n[]\\n###JOERN_JSON_END###")'

    targets_scala = _build_target_set_scala(targets)
    func_finder = _build_func_finder_scala(targets_scala)

    return f"""
import io.shiftleft.semanticcpg.language._

{func_finder}

val results = scala.collection.mutable.ListBuffer[String]()

for (funcName <- targetFuncNames) {{
  val callSites = cpg.call.name(funcName).l
  for (cs <- callSites) {{
    val callerMethod = cs.method

    // Case A: if (func(...)) {{ ... }}
    val directConds = callerMethod.ast.isControlStructure
      .where(_.condition.ast.isCall.name(funcName))
      .l
    for (cond <- directConds) {{
      results += s\"\"\"{{\"callee\":\"${{funcName}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{cs.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"type\":\"IP1\"}}\"\"\"
    }}

    // Case B: ret = func(...); if (ret ...) {{ ... }}
    val assignsToFunc = callerMethod.ast.isCall
      .name("<operator>.assignment")
      .where(_.argument(2).ast.isCall.name(funcName))
      .l
    for (asgn <- assignsToFunc) {{
      val ident = asgn.argument(1).code
      val condUses = callerMethod.ast.isControlStructure
        .where(_.condition.ast.isIdentifier.name(ident))
        .l
      for (cond <- condUses) {{
        results += s\"\"\"{{\"callee\":\"${{funcName}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{asgn.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"variable\":\"${{ident}}\",\"type\":\"IP1\"}}\"\"\"
      }}
    }}
  }}
}}

println("###JOERN_JSON_START###")
println("[" + results.mkString(",\\n") + "]")
println("###JOERN_JSON_END###")
"""


def build_ip2_query(targets):
    """IP2: out-param write near deref -> caller uses mutated arg in conditional."""
    if not targets:
        return 'println("[]")'

    targets_scala = _build_target_set_scala(targets)
    func_finder = _build_func_finder_scala(targets_scala)

    return f"""
import io.joern.dataflowengineoss.language._
import io.shiftleft.semanticcpg.language._

{func_finder}

val results = scala.collection.mutable.ListBuffer[String]()

for (funcName <- targetFuncNames) {{
  val callSites = cpg.call.name(funcName).l
  for (cs <- callSites) {{
    val callerMethod = cs.method

    // Check address-of args: func(&var); if (var ...) {{ ... }}
    val addrOfArgs = cs.argument.isCall
      .name("<operator>.addressOf")
      .argument.isIdentifier.name.l

    for (argName <- addrOfArgs) {{
      val condUses = callerMethod.ast.isControlStructure
        .where(_.condition.ast.isIdentifier.name(argName))
        .where(_.lineNumber.map(_ >= cs.lineNumber.getOrElse(0)))
        .l
      for (cond <- condUses) {{
        results += s\"\"\"{{\"callee\":\"${{funcName}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{cs.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"variable\":\"${{argName}}\",\"type\":\"IP2\"}}\"\"\"
      }}
    }}

    // Check pointer args that are dereferenced after the call
    val ptrArgs = cs.argument.isIdentifier.name.l
    for (argName <- ptrArgs) {{
      val laterDerefs = callerMethod.ast.isCall
        .name("<operator>.indirection", "<operator>.indirectMemberAccess")
        .where(_.argument.isIdentifier.name(argName))
        .where(_.lineNumber.map(_ > cs.lineNumber.getOrElse(0)))
        .l
      if (laterDerefs.nonEmpty) {{
        val condUses = callerMethod.ast.isControlStructure
          .where(_.condition.ast.isIdentifier.name(argName))
          .where(_.lineNumber.map(_ >= cs.lineNumber.getOrElse(0)))
          .l
        for (cond <- condUses) {{
          results += s\"\"\"{{\"callee\":\"${{funcName}}\",\"caller\":\"${{callerMethod.name}}\",\"callerFile\":\"${{callerMethod.filename}}\",\"callLine\":${{cs.lineNumber.getOrElse(-1)}},\"condLine\":${{cond.lineNumber.getOrElse(-1)}},\"variable\":\"${{argName}}\",\"type\":\"IP2\"}}\"\"\"
        }}
      }}
    }}
  }}
}}

println("###JOERN_JSON_START###")
println("[" + results.mkString(",\\n") + "]")
println("###JOERN_JSON_END###")
"""


def find_joern():
    for path in ["joern", os.path.expanduser("~/bin/joern/joern-cli/joern"),
                  os.path.expanduser("~/bin/joern"), "/usr/local/bin/joern"]:
        try:
            r = subprocess.run([path, "--version"], capture_output=True,
                               text=True, timeout=30)
            if r.returncode == 0:
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return ""


def count_c_files(d):
    n = 0
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith('.') and x not in
                   ('node_modules', '.git', 'test', 'tests')]
        n += sum(1 for f in files if f.endswith('.c'))
    return n


def build_cpg(joern_bin, project_dir, cpg_path):
    if os.path.exists(cpg_path):
        print(f"    CPG cached")
        return True
    n = count_c_files(project_dir)
    if n > MAX_C_FILES:
        print(f"    SKIP: {n} C files exceeds limit ({MAX_C_FILES})")
        return False
    print(f"    Building CPG ({n} C files) ...")
    parse_bin = os.path.join(os.path.dirname(joern_bin), "joern-parse")
    if not os.path.exists(parse_bin):
        parse_bin = "joern-parse"
    env = os.environ.copy()
    env["JAVA_OPTS"] = f"-Xmx{JOERN_MAX_MEMORY}"
    try:
        r = subprocess.run([parse_bin, project_dir, "--output", cpg_path],
                           capture_output=True, text=True, timeout=CPG_TIMEOUT,
                           env=env)
        if r.returncode != 0:
            real = [l for l in r.stderr.split('\n')
                    if l.strip() and not l.startswith('WARNING:')]
            if real:
                print(f"    ERROR: {real[0][:200]}")
                return False
            if os.path.exists(cpg_path):
                print(f"    CPG built (with JVM warnings)")
                return True
            print(f"    ERROR: CPG not created")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT (>{CPG_TIMEOUT}s)")
        return False


def run_joern_query(joern_bin, cpg_path, query, output_path):
    script_path = output_path + ".scala"
    with open(script_path, "w") as f:
        f.write(f'importCpg("{cpg_path}")\n')
        f.write(query)
    env = os.environ.copy()
    env["JAVA_OPTS"] = f"-Xmx{JOERN_MAX_MEMORY}"
    try:
        r = subprocess.run([joern_bin, "--script", script_path, "--nocolors"],
                           capture_output=True, text=True,
                           timeout=QUERY_TIMEOUT, env=env)
        stdout = r.stdout

        # Print diagnostic lines from stderr (our System.err.println output)
        for line in r.stderr.split('\n'):
            if line.strip().startswith('DIAG:'):
                print(f"    {line.strip()}")

        # Extract JSON between unique markers (avoids [info]/[warn] confusion)
        start_marker = "###JOERN_JSON_START###"
        end_marker = "###JOERN_JSON_END###"
        ms = stdout.find(start_marker)
        me = stdout.find(end_marker)

        if ms >= 0 and me > ms:
            json_block = stdout[ms + len(start_marker):me].strip()
            json_block = re.sub(r',\s*\]', ']', json_block)  # fix trailing commas
            try:
                data = json.loads(json_block)
                with open(output_path, "w") as f:
                    json.dump(data, f, indent=2)
                return data
            except json.JSONDecodeError as e:
                print(f"    WARNING: JSON parse error: {e}")
                print(f"    First 200 chars: {json_block[:200]}")
        else:
            # Markers not found — query may have failed to compile/run
            real = [l for l in r.stderr.split('\n')
                    if l.strip() and not l.startswith('WARNING:')]
            if real:
                print(f"    WARNING: {real[0][:200]}")
            else:
                print(f"    No results (markers not found in output)")
        with open(output_path, "w") as f:
            json.dump([], f)
        return []
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT (>{QUERY_TIMEOUT}s)")
        with open(output_path, "w") as f:
            json.dump([], f)
        return []
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)


def load_semgrep_csv(output_dir):
    p = os.path.join(output_dir, "criticality_data.csv")
    if not os.path.exists(p):
        return []
    rows = []
    with open(p) as f:
        for row in csv.DictReader(f):
            for k in row:
                if k != "project":
                    try:
                        v = float(row[k])
                        row[k] = int(v) if v == int(v) else v
                    except (ValueError, OverflowError):
                        pass
            rows.append(row)
    return rows


def extract_function_name_from_check(content: str, check_line: int) -> str:
    """Heuristic: find the enclosing function name for a line number.

    Scans backwards from check_line looking for a function definition.
    """
    lines = content.split("\n")
    func_re = re.compile(r"^(?:\w+[\s*]+)+(\w+)\s*\(")
    for i in range(min(check_line - 1, len(lines) - 1), -1, -1):
        m = func_re.match(lines[i])
        if m:
            return m.group(1)
    return ""


def count_ip_write_locations(project_name, output_dir, dataset_dir,
                             joern_results):
    """Count unique C3/C6 write LOCATIONS whose enclosing function has
    verified Joern callers.

    This matches the dev patch study's counting: write locations, not
    flow tuples or function names.

    Requires source file access to resolve (file, line) → function name.
    """
    flows = joern_results.get(project_name, [])
    ip1_callees = {f["callee"] for f in flows if f.get("type") == "IP1"}
    ip2_callees = {f["callee"] for f in flows if f.get("type") == "IP2"}

    if not ip1_callees and not ip2_callees:
        return 0, 0

    # Load semgrep raw JSON
    raw_path = os.path.join(output_dir, f"{project_name}_raw.json")
    if not os.path.exists(raw_path):
        return 0, 0
    with open(raw_path) as f:
        data = json.load(f)

    # Cache file contents to avoid re-reading
    file_cache = {}
    ip1_locations = set()
    ip2_locations = set()

    for finding in data.get("results", []):
        rule_id = finding.get("check_id", "").split(".")[-1]
        is_c3 = rule_id.startswith("C3")
        is_c6 = rule_id.startswith("C6")
        if not (is_c3 or is_c6):
            continue

        filepath = finding.get("path", "")
        line = finding.get("start", {}).get("line", 0)

        # Load source file (cached)
        if filepath not in file_cache:
            # Try filepath directly first (semgrep paths may already
            # include the dataset/ prefix), then with dataset_dir
            candidates = [filepath]
            if dataset_dir and not os.path.isabs(filepath):
                candidates.append(os.path.join(dataset_dir, filepath))
                # Also try stripping common prefixes
                for prefix in ("dataset/", "dataset\\"):
                    if filepath.startswith(prefix):
                        stripped = filepath[len(prefix):]
                        candidates.append(
                            os.path.join(dataset_dir, stripped))

            content_found = None
            for candidate in candidates:
                try:
                    with open(candidate) as f:
                        content_found = f.read()
                    break
                except (FileNotFoundError, IOError):
                    continue
            file_cache[filepath] = content_found

        content = file_cache[filepath]
        if content is None:
            continue

        func_name = extract_function_name_from_check(content, line)
        if not func_name:
            continue

        if is_c3 and func_name in ip1_callees:
            ip1_locations.add((filepath, line))
        if is_c6 and func_name in ip2_callees:
            ip2_locations.add((filepath, line))

    found = sum(1 for v in file_cache.values() if v is not None)
    missed = sum(1 for v in file_cache.values() if v is None)
    if found > 0 or missed > 0:
        print(f"    IP write-locations: {len(ip1_locations)} IP1, "
              f"{len(ip2_locations)} IP2  "
              f"(files: {found} found, {missed} missed)")

    return len(ip1_locations), len(ip2_locations)


def merge_with_semgrep(output_dir, joern_results, exclude_set=None,
                       dataset_dir=None):
    stats_path = os.path.join(output_dir, "stats.json")
    merged = json.load(open(stats_path)) if os.path.exists(stats_path) else {}
    per_project = load_semgrep_csv(output_dir)

    # Filter excluded projects from semgrep data
    if exclude_set:
        per_project = [p for p in per_project if p["project"] not in exclude_set]

    t1 = t2 = 0
    for p in per_project:
        name = p["project"]

        if dataset_dir:
            # Count write LOCATIONS with verified callers (same unit as dev study)
            ip1, ip2 = count_ip_write_locations(
                name, output_dir, dataset_dir, joern_results)
        else:
            # Fallback: count unique callee functions (approximation)
            flows = joern_results.get(name, [])
            ip1 = len({f["callee"] for f in flows if f.get("type") == "IP1"})
            ip2 = len({f["callee"] for f in flows if f.get("type") == "IP2"})

        t1 += ip1; t2 += ip2
        p["ip_return_to_cond"] = ip1
        p["ip_outparam_to_cond"] = ip2
        p["ip_total"] = ip1 + ip2
        bl = p.get("baseline_writes", 0)
        dd = p.get("dedup_critical", p.get("critical_writes", 0))
        p["extended_rate"] = (dd + ip1 + ip2) / bl if bl > 0 else 0

    merged["ip_return_to_cond"] = t1
    merged["ip_outparam_to_cond"] = t2
    merged["ip_total"] = t1 + t2

    # Recompute aggregates from the (possibly filtered) per_project list
    # so that excluding a project affects ALL aggregate numbers consistently
    bl = sum(p.get("baseline_writes", 0) for p in per_project)
    dd = sum(p.get("dedup_critical", p.get("critical_writes", 0)) for p in per_project)
    merged["total_baseline_writes"] = bl
    merged["deduplicated_critical_total"] = dd
    merged["aggregate_criticality_rate"] = dd / bl if bl > 0 else 0
    merged["extended_criticality_rate"] = (dd + t1 + t2) / bl if bl > 0 else 0
    merged["n_projects"] = len(per_project)

    # ── Combined "flows to conditional" metric ──
    # Intra: from overlap data in stats.json (not per-project, so we note
    # this is approximate when projects are excluded — the overlap matrix
    # in stats.json includes all projects)
    overlap_per_cat = merged.get("overlap_per_cat", {})
    intra_cond = overlap_per_cat.get("cond", 0)
    overlap_matrix = merged.get("overlap_matrix", {})
    # These are subsets of intra_cond (locations in BOTH categories)
    heap_to_cond = overlap_matrix.get("cond+heap", overlap_matrix.get("cond+heap", 0))
    rsrc_to_cond = overlap_matrix.get("cond+rsrc", overlap_matrix.get("cond+rsrc", 0))
    ret_to_cond = overlap_matrix.get("cond+ret", overlap_matrix.get("cond+ret", 0))
    call_to_cond = overlap_matrix.get("call+cond", overlap_matrix.get("call+cond", 0))
    io_to_cond = overlap_matrix.get("cond+io", overlap_matrix.get("cond+io", 0))

    merged["intra_cond_total"] = intra_cond
    merged["intra_cond_compound"] = heap_to_cond + rsrc_to_cond + ret_to_cond + call_to_cond + io_to_cond
    merged["intra_cond_pure"] = intra_cond  # all unique cond locations (compounds are subsets)
    merged["inter_cond_total"] = t1 + t2
    merged["total_cond_flows"] = intra_cond + t1 + t2
    if bl > 0:
        merged["total_cond_rate"] = (intra_cond + t1 + t2) / bl
    else:
        merged["total_cond_rate"] = 0

    merged["per_project"] = per_project
    return merged


def print_summary(m):
    t1 = m.get("ip_return_to_cond", 0)
    t2 = m.get("ip_outparam_to_cond", 0)
    tip = m.get("ip_total", 0)
    bl = m.get("total_baseline_writes", 0)
    dd = m.get("deduplicated_critical_total", 0)
    ir = m.get("aggregate_criticality_rate", 0)
    er = m.get("extended_criticality_rate", 0)
    delta = er - ir

    intra_cond = m.get("intra_cond_total", 0)
    inter_cond = m.get("inter_cond_total", 0)
    total_cond = m.get("total_cond_flows", 0)
    total_cond_rate = m.get("total_cond_rate", 0)

    # Overlap breakdown (what flows into cond intra-procedurally)
    ov = m.get("overlap_matrix", {})
    heap_cond = ov.get("cond+heap", 0)
    rsrc_cond = ov.get("cond+rsrc", 0)
    ret_cond = ov.get("cond+ret", 0)
    call_cond = ov.get("call+cond", 0)
    io_cond = ov.get("cond+io", 0)

    print("\n" + "=" * 70)
    print(" COMBINED ANALYSIS: ALL FLOWS TO CONDITIONAL")
    print("=" * 70)
    print()
    print("  ── Intra-procedural: writes flowing to a conditional (semgrep) ──")
    print(f"    Total unique locations:                 {intra_cond:>6,}")
    if bl > 0:
        print(f"    As % of baseline writes:                {intra_cond/bl*100:>5.1f}%")
    print(f"    Of those, how many also have another critical property:")
    print(f"      Also a heap write:                     {heap_cond:>6,}")
    print(f"      Also resource mgmt (malloc/free):      {rsrc_cond:>6,}")
    print(f"      Also feeds a return:                   {ret_cond:>6,}")
    print(f"      Also escapes via call:                 {call_cond:>6,}")
    print(f"      Also feeds I/O:                        {io_cond:>6,}")
    print(f"    (These overlap — a single location can have multiple properties)")
    print()
    print("  ── Inter-procedural: writes flowing to a caller conditional (Joern) ──")
    print(f"    IP1 (return → caller conditional):       {t1:>6,}")
    print(f"    IP2 (out-param → caller conditional):    {t2:>6,}")
    print(f"    Total inter-procedural:                  {inter_cond:>6,}")
    if bl > 0:
        print(f"    As % of baseline writes:                {inter_cond/bl*100:>5.1f}%")
    print()
    print("  ── TOTAL: ALL WRITES FLOWING TO A CONDITIONAL ──")
    print(f"    Intra (same function):   {intra_cond:>6,}")
    print(f"    + Inter (cross-function):{inter_cond:>6,}")
    print(f"    = Total:                 {total_cond:>6,}")
    if bl > 0:
        print(f"    As % of baseline:        {total_cond_rate*100:.1f}%  "
              f"({total_cond:,} / {bl:,})")
    print()
    print("  ── OVERALL CRITICALITY ──")
    print(f"    Intra-procedural (dedup):  {ir*100:.1f}%  ({dd:,} / {bl:,})")
    print(f"    Extended (+ Joern):        {er*100:.1f}%  ({dd+tip:,} / {bl:,})")
    print(f"    Delta:                     +{delta*100:.1f}pp")
    print()

    pp = m.get("per_project", [])
    if pp:
        print("  ── Per-project inter-procedural flows ──")
        print(f"    {'Project':20s} {'IP1':>6s} {'IP2':>5s} {'Total':>5s} "
              f"{'Intra%':>6s} {'Ext%':>6s} {'dpp':>5s}")
        print("    " + "-" * 60)
        for p in sorted(pp, key=lambda x: x["project"]):
            if p.get("baseline_writes", 0) == 0:
                continue
            i1 = p.get("ip_return_to_cond", 0)
            i2 = p.get("ip_outparam_to_cond", 0)
            it = p.get("ip_total", 0)
            inr = p.get("criticality_rate", 0) * 100
            exr = p.get("extended_rate", 0) * 100
            d = exr - inr
            print(f"    {p['project']:20s} {i1:6d} {i2:5d} {it:5d} "
                  f"{inr:5.1f}% {exr:5.1f}% {d:+4.1f}")

    print()
    dp = delta * 100
    if dp < 2:
        print(f"  -> +{dp:.1f}pp: Local safety is robust.")
    elif dp < 8:
        print(f"  -> +{dp:.1f}pp: Modest cross-functional impact.")
    else:
        print(f"  -> +{dp:.1f}pp: Significant cross-functional impact.")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="./dataset")
    ap.add_argument("--output", default="./results")
    ap.add_argument("--project", default=None)
    ap.add_argument("--joern", default=None)
    ap.add_argument("--reprocess", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="Comma-separated project names to exclude "
                         "(e.g., --exclude cpython)")
    ap.add_argument("--max-memory", default=JOERN_MAX_MEMORY)
    ap.add_argument("--query-timeout", type=int, default=QUERY_TIMEOUT)
    args = ap.parse_args()

    # Module-level config (read by build_cpg / run_joern_query)
    this = sys.modules[__name__]
    this.JOERN_MAX_MEMORY = args.max_memory
    this.QUERY_TIMEOUT = args.query_timeout

    exclude_set = set(x.strip() for x in args.exclude.split(",") if x.strip())
    if exclude_set:
        print(f"Excluding projects: {', '.join(sorted(exclude_set))}")

    os.makedirs(args.output, exist_ok=True)
    jd = os.path.join(args.output, "joern")
    os.makedirs(jd, exist_ok=True)

    jr = {}  # project -> flows

    if args.reprocess:
        for f in sorted(Path(jd).glob("*_interproc.json")):
            name = f.stem.replace("_interproc", "")
            if args.project and name != args.project:
                continue
            if name in exclude_set:
                continue
            jr[name] = json.load(open(f))
            i1 = len([x for x in jr[name] if x.get("type") == "IP1"])
            i2 = len([x for x in jr[name] if x.get("type") == "IP2"])
            print(f"  [{name}] cached: IP1={i1}  IP2={i2}")
    else:
        jb = args.joern or find_joern()
        if not jb:
            print("ERROR: Joern not found. --joern /path/to/joern")
            sys.exit(1)
        print(f"Joern: {jb}  Memory: {JOERN_MAX_MEMORY}")

        dp = Path(args.dataset)
        projects = ([dp / args.project] if args.project else
                    sorted(p for p in dp.iterdir()
                           if p.is_dir() and not p.name.startswith(".")))

        for pd in projects:
            name = pd.name
            if name in exclude_set:
                print(f"\n[{name}] EXCLUDED")
                continue
            print(f"\n[{name}]")
            out = os.path.join(jd, f"{name}_interproc.json")

            if os.path.exists(out):
                jr[name] = json.load(open(out))
                i1 = len([x for x in jr[name] if x.get("type") == "IP1"])
                i2 = len([x for x in jr[name] if x.get("type") == "IP2"])
                print(f"    Cached: IP1={i1}  IP2={i2}")
                continue

            # Extract targets from semgrep
            sr = os.path.join(args.output, f"{name}_raw.json")
            tgt = extract_target_functions(sr)
            nr = sum(len(v) for v in tgt["return_feeding"].values())
            no = sum(len(v) for v in tgt["outparam"].values())
            print(f"    Semgrep targets: {nr} return-feeding, {no} out-param")

            if nr == 0 and no == 0:
                print(f"    No targets -- skip Joern")
                jr[name] = []
                json.dump([], open(out, "w"))
                continue

            # Build CPG
            cpg = os.path.join(jd, "workspace", f"{name}.bin")
            os.makedirs(os.path.dirname(cpg), exist_ok=True)
            if not build_cpg(jb, str(pd), cpg):
                jr[name] = []
                json.dump([], open(out, "w"))
                continue

            combined = []
            if nr > 0:
                # Two-step IP1: resolve function names, then query
                print("    Resolving IP1 target functions ...")
                resolve_q = build_resolve_functions_query(tgt["return_feeding"])
                resolve_path = os.path.join(jd, f"{name}_ip1_funcs.json")
                func_names = run_joern_query(jb, cpg, resolve_q, resolve_path)
                print(f"    Resolved {len(func_names)} target functions")

                if func_names:
                    print("    Running IP1 ...")
                    q = build_ip1_query_from_names(func_names)
                    r = run_joern_query(jb, cpg, q, os.path.join(jd, f"{name}_ip1.json"))
                    combined.extend(r)
                    print(f"    IP1: {len(r)} flows")
                else:
                    print("    IP1: 0 flows (no target functions resolved)")

            if no > 0:
                print("    Running IP2 ...")
                q = build_ip2_query(tgt["outparam"])
                r = run_joern_query(jb, cpg, q, os.path.join(jd, f"{name}_ip2.json"))
                combined.extend(r)
                print(f"    IP2: {len(r)} flows")

            # Dedup
            seen = set()
            deduped = []
            for fl in combined:
                k = (fl.get("callerFile",""), fl.get("callLine",-1),
                     fl.get("condLine",-1), fl.get("type",""))
                if k not in seen:
                    seen.add(k)
                    deduped.append(fl)

            jr[name] = deduped
            json.dump(deduped, open(out, "w"), indent=2)
            i1 = len([x for x in deduped if x.get("type") == "IP1"])
            i2 = len([x for x in deduped if x.get("type") == "IP2"])
            print(f"    Final: IP1={i1}  IP2={i2}")

    merged = merge_with_semgrep(args.output, jr, exclude_set,
                               dataset_dir=args.dataset)
    print_summary(merged)

    mp = os.path.join(args.output, "stats_extended.json")
    ms = {k: v for k, v in merged.items() if k not in ("per_project", "cat_short")}
    json.dump(ms, open(mp, "w"), indent=2)
    print(f"\nExtended stats: {mp}")

    cp = os.path.join(args.output, "criticality_extended.csv")
    pp = merged.get("per_project", [])
    if pp:
        flds = ["project", "baseline_writes", "dedup_critical",
                "criticality_rate", "ip_return_to_cond",
                "ip_outparam_to_cond", "ip_total", "extended_rate"]
        w = csv.DictWriter(open(cp, "w", newline=""), flds, extrasaction="ignore")
        w.writeheader()
        for p in pp:
            w.writerow(p)
        print(f"Extended CSV: {cp}")


if __name__ == "__main__":
    main()
