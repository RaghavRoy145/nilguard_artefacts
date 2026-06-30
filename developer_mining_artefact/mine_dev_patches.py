#!/usr/bin/env python3
"""
mine_dev_patches.py — Mine developer NPE patches from git history.

Finds commits where developers added null checks, extracts the guarded
scope, and runs the same criticality analysis to compare developer
patches with NilGuard's approach.

PIPELINE:
  1. Search commit messages for NPE-related terms (local git, no API needed)
  2. Extract diffs of C files from matching commits
  3. Parse diffs to find added null checks (if (ptr == NULL), if (!ptr), etc.)
  4. For each added guard, extract the post-patch file
  5. Run semgrep on the post-patch file to classify writes in the guarded scope
  6. Compute criticality rate of developer patches
  7. Compare with baseline study

Usage:
    python3 mine_dev_patches.py --dataset ./dataset --output ./results/dev_patches
    python3 mine_dev_patches.py --dataset ./dataset --output ./results/dev_patches --project redis
    python3 mine_dev_patches.py --reprocess --output ./results/dev_patches
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# ── Search terms for NPE-related commits ─────────────────────────────────

# These are matched case-insensitively against commit messages.
# Designed for high recall; false positives are filtered in later stages.
NPE_SEARCH_TERMS = [
    r"null pointer",
    r"null dereference",
    r"null deref",
    r"null check",
    r"null guard",
    r"NPE",
    r"SIGSEGV",
    r"segfault",
    r"segmentation fault",
    r"fix.*null",
    r"null.*fix",
    r"null.*crash",
    r"crash.*null",
    r"check.*null",
    r"NULL.*bug",
    r"dereference.*null",
    r"avoid.*null",
    r"prevent.*null",
    r"handle.*null",
    r"missing.*null",
    r"add.*null.*check",
]

# Regex to detect added null checks in diffs
# Matches lines like: +  if (ptr == NULL), +  if (!ptr), +  if (ptr != NULL),
#                      +  if (NULL == ptr), +  if (x == NULL) return;
NULL_CHECK_PATTERNS = [
    # if (ptr == NULL) or if (NULL == ptr)
    r"^\+\s*if\s*\(\s*\w+\s*==\s*NULL\b",
    r"^\+\s*if\s*\(\s*NULL\s*==\s*\w+",
    # if (ptr != NULL)
    r"^\+\s*if\s*\(\s*\w+\s*!=\s*NULL\b",
    r"^\+\s*if\s*\(\s*NULL\s*!=\s*\w+",
    # if (!ptr) or if (ptr)
    r"^\+\s*if\s*\(\s*!\s*\w+\s*\)",
    r"^\+\s*if\s*\(\s*\w+\s*\)\s*\{",
    # ptr == NULL in a ternary or compound condition
    r"^\+.*\w+\s*==\s*NULL",
    r"^\+.*\w+\s*!=\s*NULL",
]

NULL_CHECK_RE = re.compile("|".join(NULL_CHECK_PATTERNS), re.IGNORECASE)

# Bound on how far back to search (commits)
MAX_COMMITS_PER_PROJECT = 50000

# Bound on total NPE commits to process per project
MAX_NPE_COMMITS_PER_PROJECT = 200

# Projects to skip
SKIP_PROJECTS = {"cpython"}


def search_npe_commits(repo_dir: str, max_commits: int = MAX_COMMITS_PER_PROJECT) -> list:
    """Search git history for NPE-related commits.

    Uses git log --grep with multiple patterns (OR'd together).
    Returns list of (commit_hash, subject, date) tuples.
    """
    # Build grep pattern: git log --grep uses basic regex, OR via \|
    grep_terms = [
        "null pointer", "null dereference", "null deref", "null check",
        "NPE", "SIGSEGV", "segfault", "segmentation fault",
        "fix.*null", "null.*crash", "null.*bug",
        "missing null", "add.*null", "avoid.*null",
        "handle.*null", "prevent.*null", "check.*NULL",
    ]

    commits = set()
    for term in grep_terms:
        cmd = [
            "git", "-C", repo_dir, "log",
            f"--max-count={max_commits}",
            "--grep", term,
            "-i",  # case insensitive
            "--diff-filter=M",  # only modified files
            "--format=%H\t%aI\t%s",
            "--", "*.c",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=120)
            for line in result.stdout.strip().split("\n"):
                if "\t" in line:
                    parts = line.split("\t", 2)
                    if len(parts) == 3:
                        commits.add(tuple(parts))
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"    WARNING: git log failed for term '{term}': {e}")

    # Sort by date descending
    commits = sorted(commits, key=lambda x: x[1], reverse=True)
    return commits[:MAX_NPE_COMMITS_PER_PROJECT]


def get_commit_diff(repo_dir: str, commit_hash: str) -> str:
    """Get the diff for a specific commit, limited to C files."""
    cmd = [
        "git", "-C", repo_dir, "diff",
        f"{commit_hash}^..{commit_hash}",
        "--", "*.c",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout
    except Exception:
        return ""


def get_file_at_commit(repo_dir: str, commit_hash: str, filepath: str) -> str:
    """Get file contents at a specific commit."""
    cmd = ["git", "-C", repo_dir, "show", f"{commit_hash}:{filepath}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout
        return ""
    except Exception:
        return ""


def parse_diff_for_null_checks(diff_text: str) -> list:
    """Parse a unified diff to find added null checks.

    Stores the diff hunk context around each null check for manual inspection.
    """
    null_checks = []
    current_file = None
    post_line = 0
    hunk_start = 0
    current_hunk_lines = []

    for line in diff_text.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("--- "):
            continue

        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match:
            post_line = int(hunk_match.group(1)) - 1
            hunk_start = int(hunk_match.group(1))
            current_hunk_lines = [line]
            continue

        current_hunk_lines.append(line)

        if line.startswith("-"):
            continue
        elif line.startswith("+"):
            post_line += 1
        else:
            post_line += 1

        if line.startswith("+") and current_file and NULL_CHECK_RE.match(line):
            # Capture surrounding context (up to 10 lines before/after in hunk)
            hunk_idx = len(current_hunk_lines) - 1
            ctx_start = max(0, hunk_idx - 10)
            ctx_end = min(len(current_hunk_lines), hunk_idx + 11)
            context = "\n".join(current_hunk_lines[ctx_start:ctx_end])

            null_checks.append({
                "file": current_file,
                "line": post_line,
                "check_text": line[1:].strip(),
                "hunk_start": hunk_start,
                "diff_context": context,
            })

    return null_checks


def get_modified_c_files(repo_dir: str, commit_hash: str) -> list:
    """Get list of modified C files in a commit."""
    cmd = [
        "git", "-C", repo_dir, "diff", "--name-only",
        "--diff-filter=M", f"{commit_hash}^..{commit_hash}",
        "--", "*.c",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        return []


def run_semgrep_on_content(content: str, rules_path: str,
                           filename: str = "patch.c") -> dict:
    """Run semgrep on file content (written to a temp file).

    Returns parsed JSON results. Returns empty on timeout or error.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".c",
                                     prefix="dev_patch_",
                                     delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        cmd = [
            "semgrep",
            "--config", rules_path,
            "--json",
            "--timeout", "120",
            "--max-target-bytes", "500000",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"results": []}
    except subprocess.TimeoutExpired:
        return {"results": []}
    except Exception:
        return {"results": []}
    finally:
        os.unlink(tmp_path)


def _init_tree_sitter():
    """Lazily initialize tree-sitter C parser."""
    global _ts_parser
    if "_ts_parser" not in globals() or _ts_parser is None:
        try:
            import tree_sitter_c as tsc
            from tree_sitter import Language, Parser
            lang = Language(tsc.language())
            _ts_parser = Parser(lang)
        except ImportError:
            print("WARNING: tree-sitter-c not installed. "
                  "Falling back to ±30-line heuristic.")
            print("  pip install tree-sitter tree-sitter-c")
            _ts_parser = None
    return _ts_parser

_ts_parser = None
SCOPE_FALLBACK_RADIUS = 30  # used only when tree-sitter fails


def extract_guard_scope(content: str, check_line: int) -> tuple:
    """Extract the actual scope of a null-check guard using tree-sitter.

    Handles three patterns:
      1. Early return: if (ptr == NULL) return ...;
         → scope = (check_line+1, end_of_enclosing_function)
      2. Early goto:   if (ptr == NULL) goto err;
         → scope = (check_line+1, end_of_enclosing_function)
      3. Guard block:  if (ptr != NULL) { ... }
         → scope = (start_of_block, end_of_block)

    Returns (start_line, end_line) of the guarded scope (1-indexed).
    Falls back to ±SCOPE_FALLBACK_RADIUS if parsing fails.
    """
    parser = _init_tree_sitter()
    if parser is None:
        return (max(1, check_line - SCOPE_FALLBACK_RADIUS),
                check_line + SCOPE_FALLBACK_RADIUS)

    try:
        tree = parser.parse(content.encode("utf-8", errors="replace"))
    except Exception:
        return (max(1, check_line - SCOPE_FALLBACK_RADIUS),
                check_line + SCOPE_FALLBACK_RADIUS)

    # Find the if_statement at check_line (0-indexed in tree-sitter)
    target_line = check_line - 1

    def find_if_at_line(node):
        if node.type == "if_statement" and node.start_point[0] == target_line:
            return node
        for child in node.children:
            result = find_if_at_line(child)
            if result:
                return result
        return None

    def find_enclosing_function(node, target):
        """Walk up to find the function_definition containing target line."""
        if node.type == "function_definition":
            if node.start_point[0] <= target <= node.end_point[0]:
                return node
        for child in node.children:
            result = find_enclosing_function(child, target)
            if result:
                return result
        return None

    if_node = find_if_at_line(tree.root_node)
    if if_node is None:
        # Try ±1 line (diff line counting may be slightly off)
        for offset in [-1, 1, -2, 2]:
            target_line = check_line - 1 + offset
            if_node = find_if_at_line(tree.root_node)
            if if_node:
                break

    if if_node is None:
        return (max(1, check_line - SCOPE_FALLBACK_RADIUS),
                check_line + SCOPE_FALLBACK_RADIUS)

    consequence = if_node.child_by_field_name("consequence")
    if consequence is None:
        return (max(1, check_line - SCOPE_FALLBACK_RADIUS),
                check_line + SCOPE_FALLBACK_RADIUS)

    # Determine the pattern
    cons_type = consequence.type

    # Early return / goto: consequence is return_statement, goto_statement,
    # or a block containing just a return/goto
    is_early_exit = False
    if cons_type in ("return_statement", "goto_statement", "break_statement"):
        is_early_exit = True
    elif cons_type == "compound_statement":
        # Check if the block contains only a return/goto (+ braces)
        non_brace = [c for c in consequence.children
                     if c.type not in ("{", "}", "comment")]
        if len(non_brace) == 1 and non_brace[0].type in (
                "return_statement", "goto_statement", "break_statement",
                "expression_statement"):
            is_early_exit = True

    if is_early_exit:
        # Scope = everything after this if-statement to end of enclosing function
        func = find_enclosing_function(tree.root_node, target_line)
        if func:
            scope_start = if_node.end_point[0] + 2  # 1-indexed, line after the if
            scope_end = func.end_point[0] + 1       # 1-indexed
            return (scope_start, scope_end)
        else:
            # No function found, use generous fallback
            total_lines = content.count("\n") + 1
            return (if_node.end_point[0] + 2, min(total_lines, check_line + 200))
    else:
        # Guard block: scope is the consequence block itself
        scope_start = consequence.start_point[0] + 1  # 1-indexed
        scope_end = consequence.end_point[0] + 1      # 1-indexed
        return (scope_start, scope_end)


def analyze_patch_criticality(semgrep_results: dict,
                              null_check_lines: list,
                              file_content: str = None) -> dict:
    """Analyze semgrep results for writes within the developer's guard scope.

    Uses tree-sitter to extract the actual scope of each null check guard,
    replacing the previous ±30-line heuristic.
    """
    # Build set of lines in the guarded scope(s)
    guarded_lines = set()
    scope_info = []

    for nc in null_check_lines:
        line = nc["line"]
        if file_content:
            start, end = extract_guard_scope(file_content, line)
        else:
            start = max(1, line - SCOPE_FALLBACK_RADIUS)
            end = line + SCOPE_FALLBACK_RADIUS
        scope_info.append({"check_line": line, "scope_start": start,
                           "scope_end": end, "scope_size": end - start + 1})
        for l in range(start, end + 1):
            guarded_lines.add(l)

    stats = {
        "total_writes_near_guard": 0,
        "critical_writes_near_guard": 0,
        "cond_writes_near_guard": 0,
        "categories": defaultdict(int),
        "scope_info": scope_info,
    }

    CRIT_CATS = {
        "C1": "heap", "C2": "cond", "C3": "ret", "C4": "call",
        "C5": "io", "C6": "heap", "C7": "rsrc", "C8": "rsrc",
    }
    BASELINE_PREFIXES = ("B1", "B2", "B3", "B4", "B5", "B6", "B7")
    CRIT_PREFIXES = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")

    crit_locations = set()

    for finding in semgrep_results.get("results", []):
        rule_id = finding.get("check_id", "").split(".")[-1]
        line = finding.get("start", {}).get("line", 0)

        if line not in guarded_lines:
            continue

        if rule_id.startswith(BASELINE_PREFIXES):
            stats["total_writes_near_guard"] += 1

        if rule_id.startswith(CRIT_PREFIXES):
            crit_locations.add(line)
            for prefix, cat in CRIT_CATS.items():
                if rule_id.startswith(prefix):
                    stats["categories"][cat] += 1
                    if cat == "cond":
                        stats["cond_writes_near_guard"] += 1
                    break

    stats["critical_writes_near_guard"] = len(crit_locations)
    return stats


def load_joern_results(joern_dir: str, project_name: str) -> dict:
    """Load existing Joern IP1/IP2 results for cross-referencing.

    Returns dict mapping callee function names to their IP flow counts.
    We already have these from the main study — no additional Joern runs.
    """
    interproc_path = os.path.join(joern_dir, f"{project_name}_interproc.json")
    if not os.path.exists(interproc_path):
        return {"ip1_by_callee": {}, "ip2_by_callee": {}}

    with open(interproc_path) as f:
        flows = json.load(f)

    ip1_by_callee = defaultdict(int)
    ip2_by_callee = defaultdict(int)

    for flow in flows:
        callee = flow.get("callee", "")
        if flow.get("type") == "IP1":
            ip1_by_callee[callee] += 1
        elif flow.get("type") == "IP2":
            ip2_by_callee[callee] += 1

    return {
        "ip1_by_callee": dict(ip1_by_callee),
        "ip2_by_callee": dict(ip2_by_callee),
    }


def extract_function_name_from_check(content: str, check_line: int) -> str:
    """Heuristic: find the enclosing function name for a line number.

    Scans backwards from check_line looking for a function definition pattern.
    """
    lines = content.split("\n")
    func_re = re.compile(r"^(?:\w+[\s*]+)+(\w+)\s*\(")
    for i in range(min(check_line - 1, len(lines) - 1), -1, -1):
        m = func_re.match(lines[i])
        if m:
            return m.group(1)
    return ""


def process_project(repo_dir: str, project_name: str,
                    rules_path: str, output_dir: str,
                    joern_dir: str = None) -> dict:
    """Process a single project: find NPE commits, analyze patches."""
    print(f"\n[{project_name}]")

    # Check for cached results
    cache_path = os.path.join(output_dir, f"{project_name}_patches.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        n = len(cached.get("patches", []))
        print(f"    Cached: {n} patches")
        return cached

    # Load existing Joern results for inter-procedural cross-reference
    joern_data = {}
    if joern_dir:
        joern_data = load_joern_results(joern_dir, project_name)
        n_ip1 = len(joern_data["ip1_by_callee"])
        n_ip2 = len(joern_data["ip2_by_callee"])
        if n_ip1 or n_ip2:
            print(f"    Joern data: {n_ip1} IP1 callees, {n_ip2} IP2 callees")

    # Step 1: Find NPE-related commits
    print(f"    Searching commit history ...")
    commits = search_npe_commits(repo_dir)
    print(f"    Found {len(commits)} NPE-related commits")

    if not commits:
        result = {"project": project_name, "total_commits": 0,
                  "patches": [], "all_null_checks": []}
        with open(cache_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    # Step 2: For each commit, extract diff and find null checks
    patches = []
    all_null_checks = []  # raw manifest for manual inspection
    unmatched_commits = []  # commits where no null check pattern was found

    for commit_hash, date, subject in commits:
        diff = get_commit_diff(repo_dir, commit_hash)
        if not diff:
            continue

        null_checks = parse_diff_for_null_checks(diff)
        if not null_checks:
            # NPE-related commit but no recognized null check in diff.
            # Store for manual inspection — our regex may have missed
            # compound checks, macro guards, or struct field checks.
            # Only store the added lines from the diff (not the full diff)
            added_lines = [l for l in diff.split("\n")
                           if l.startswith("+") and not l.startswith("+++")]
            # Limit stored diff to avoid huge JSON
            diff_summary = "\n".join(added_lines[:50])
            if len(added_lines) > 50:
                diff_summary += f"\n... ({len(added_lines) - 50} more added lines)"

            unmatched_commits.append({
                "commit": commit_hash[:12],
                "date": date,
                "subject": subject[:100],
                "added_lines_count": len(added_lines),
                "diff_preview": diff_summary,
            })
            continue

        # Store raw null checks for manifest
        for nc in null_checks:
            all_null_checks.append({
                "commit": commit_hash[:12],
                "date": date,
                "subject": subject[:100],
                "file": nc["file"],
                "line": nc["line"],
                "check_text": nc["check_text"],
                "diff_context": nc.get("diff_context", ""),
            })

        # Step 3: Get post-patch files and run semgrep
        modified_files = get_modified_c_files(repo_dir, commit_hash)
        file_results = {}

        for filepath in modified_files:
            file_checks = [nc for nc in null_checks if nc["file"] == filepath]
            if not file_checks:
                continue

            content = get_file_at_commit(repo_dir, commit_hash, filepath)
            if not content:
                continue

            # Run semgrep on the post-patch file
            semgrep_data = run_semgrep_on_content(content, rules_path)

            # Analyze criticality within the developer's guard scope
            crit_stats = analyze_patch_criticality(
                semgrep_data, file_checks, file_content=content)

            # Cross-reference with Joern, but ONLY if the developer's
            # guard scope contains a return-feeding (C3) or out-param (C6)
            # write.  Count WRITES with IP impact, not flow tuples, so
            # the unit matches intra-procedural write counts.
            ip_writes = 0  # writes in scope with inter-procedural impact
            if joern_data:
                # Build the set of guarded lines from scope_info
                guarded_lines = set()
                for si in crit_stats.get("scope_info", []):
                    for l in range(si["scope_start"], si["scope_end"] + 1):
                        guarded_lines.add(l)

                # Find C3/C6 write locations within the guard scope
                c3_lines_in_scope = set()
                c6_lines_in_scope = set()
                for finding in semgrep_data.get("results", []):
                    rule_id = finding.get("check_id", "").split(".")[-1]
                    line = finding.get("start", {}).get("line", 0)
                    if line not in guarded_lines:
                        continue
                    if rule_id.startswith("C3"):
                        c3_lines_in_scope.add(line)
                    if rule_id.startswith("C6"):
                        c6_lines_in_scope.add(line)

                # Check if the enclosing function has Joern callers
                for nc in file_checks:
                    func_name = extract_function_name_from_check(
                        content, nc["line"])
                    if func_name:
                        has_ip1 = joern_data["ip1_by_callee"].get(
                            func_name, 0) > 0
                        has_ip2 = joern_data["ip2_by_callee"].get(
                            func_name, 0) > 0
                        # Count writes (not flow tuples) that have IP impact
                        if has_ip1:
                            ip_writes += len(c3_lines_in_scope)
                        if has_ip2:
                            ip_writes += len(c6_lines_in_scope)

            file_results[filepath] = {
                "null_checks": len(file_checks),
                "check_lines": [nc["line"] for nc in file_checks],
                "check_texts": [nc["check_text"] for nc in file_checks],
                "diff_contexts": [nc.get("diff_context", "")
                                  for nc in file_checks],
                "ip_writes": ip_writes,
                **crit_stats,
            }

        if file_results:
            total_writes = sum(r["total_writes_near_guard"]
                              for r in file_results.values())
            total_crit = sum(r["critical_writes_near_guard"]
                            for r in file_results.values())
            total_cond = sum(r["cond_writes_near_guard"]
                            for r in file_results.values())
            total_ip_writes = sum(r.get("ip_writes", 0)
                                  for r in file_results.values())

            # Combined conditional: intra (C2) + inter (C3/C6 with callers)
            # Both are in units of write locations
            combined_cond = total_cond + total_ip_writes

            patches.append({
                "commit": commit_hash[:12],
                "date": date,
                "subject": subject[:100],
                "files": len(file_results),
                "null_checks": sum(r["null_checks"]
                                   for r in file_results.values()),
                "total_writes": total_writes,
                "critical_writes": total_crit,
                "cond_writes": total_cond,
                "ip_writes": total_ip_writes,
                "combined_cond": combined_cond,
                "criticality_rate": total_crit / total_writes
                    if total_writes > 0 else 0,
                "cond_rate": total_cond / total_writes
                    if total_writes > 0 else 0,
                "combined_cond_rate": combined_cond / total_writes
                    if total_writes > 0 else 0,
                "file_details": file_results,
            })

    print(f"    Analyzed {len(patches)} patches with null checks "
          f"({len(all_null_checks)} raw null checks found, "
          f"{len(unmatched_commits)} commits with no check detected)")

    result = {
        "project": project_name,
        "total_commits": len(commits),
        "patches_with_null_checks": len(patches),
        "unmatched_commits": len(unmatched_commits),
        "patches": patches,
        "all_null_checks": all_null_checks,
    }

    with open(cache_path, "w") as f:
        json.dump(result, f, indent=2)

    # Save human-readable patch manifest
    manifest_path = os.path.join(output_dir,
                                 f"{project_name}_null_checks.txt")
    with open(manifest_path, "w") as f:
        f.write(f"# Null checks added in {project_name}\n")
        f.write(f"# Total: {len(all_null_checks)} null checks "
                f"from {len(patches)} commits\n\n")
        for nc in all_null_checks:
            f.write(f"--- {nc['commit']} {nc['date'][:10]} ---\n")
            f.write(f"    {nc['subject']}\n")
            f.write(f"    {nc['file']}:{nc['line']}\n")
            f.write(f"    {nc['check_text']}\n")
            if nc.get("diff_context"):
                f.write(f"    Context:\n")
                for ctx_line in nc["diff_context"].split("\n"):
                    f.write(f"      {ctx_line}\n")
            f.write("\n")

    # Save unmatched commits (NPE-related but no null check detected)
    if unmatched_commits:
        unmatched_path = os.path.join(output_dir,
                                      f"{project_name}_unmatched.txt")
        with open(unmatched_path, "w") as f:
            f.write(f"# Unmatched NPE commits in {project_name}\n")
            f.write(f"# These commits matched NPE search terms but no\n")
            f.write(f"# null-check pattern was detected in the diff.\n")
            f.write(f"# Possible reasons: compound checks (ptr && ptr->f),\n")
            f.write(f"#   macro guards, struct field checks, assert(),\n")
            f.write(f"#   or the commit is not actually an NPE fix.\n")
            f.write(f"# Total: {len(unmatched_commits)} commits\n\n")

            for uc in unmatched_commits:
                f.write(f"--- {uc['commit']} {uc['date'][:10]} ---\n")
                f.write(f"    {uc['subject']}\n")
                f.write(f"    Added lines: {uc['added_lines_count']}\n")
                if uc.get("diff_preview"):
                    f.write(f"    Diff preview:\n")
                    for dl in uc["diff_preview"].split("\n"):
                        f.write(f"      {dl}\n")
                f.write("\n")

    return result


def compute_aggregate(all_results: list) -> dict:
    """Compute aggregate statistics across all projects.

    All metrics are in the same unit: write locations.
    - cond_writes: intra-procedural (C2 matches in guard scope)
    - ip_writes: inter-procedural (C3/C6 matches with verified callers)
    - combined_cond: cond_writes + ip_writes
    """
    total_patches = 0
    total_writes = 0
    total_crit = 0
    total_cond = 0
    total_ip_writes = 0
    total_unmatched = 0
    per_project = []

    for proj in all_results:
        proj_writes = sum(p["total_writes"] for p in proj["patches"])
        proj_crit = sum(p["critical_writes"] for p in proj["patches"])
        proj_cond = sum(p["cond_writes"] for p in proj["patches"])
        proj_ip = sum(p.get("ip_writes", 0) for p in proj["patches"])
        proj_combined = sum(p.get("combined_cond", p.get("cond_writes", 0))
                           for p in proj["patches"])
        n_patches = len(proj["patches"])
        n_null_checks = len(proj.get("all_null_checks", []))
        n_unmatched = proj.get("unmatched_commits", 0)

        total_patches += n_patches
        total_writes += proj_writes
        total_crit += proj_crit
        total_cond += proj_cond
        total_ip_writes += proj_ip
        total_unmatched += n_unmatched

        per_project.append({
            "project": proj["project"],
            "npe_commits": proj["total_commits"],
            "patches": n_patches,
            "null_checks_found": n_null_checks,
            "unmatched": n_unmatched,
            "writes": proj_writes,
            "critical": proj_crit,
            "cond": proj_cond,
            "ip_writes": proj_ip,
            "combined_cond": proj_combined,
            "crit_rate": proj_crit / proj_writes if proj_writes > 0 else 0,
            "cond_rate": proj_cond / proj_writes if proj_writes > 0 else 0,
            "combined_rate": proj_combined / proj_writes
                if proj_writes > 0 else 0,
        })

    agg_crit_rate = total_crit / total_writes if total_writes > 0 else 0
    agg_cond_rate = total_cond / total_writes if total_writes > 0 else 0
    total_combined = total_cond + total_ip_writes
    agg_combined_rate = total_combined / total_writes if total_writes > 0 else 0

    return {
        "total_patches": total_patches,
        "total_unmatched": total_unmatched,
        "total_writes": total_writes,
        "total_critical": total_crit,
        "total_cond": total_cond,
        "total_ip_writes": total_ip_writes,
        "total_combined_cond": total_combined,
        "aggregate_crit_rate": agg_crit_rate,
        "aggregate_cond_rate": agg_cond_rate,
        "aggregate_combined_rate": agg_combined_rate,
        "per_project": per_project,
    }


def print_summary(agg: dict, baseline_combined_rate: float = 0.23,
                  baseline_intra_rate: float = 0.086):
    """Print comparison summary.

    All rates are in the same unit: writes / writes.
    - Intra-proc: C2 matches in guard scope / total writes in scope
    - Inter-proc: C3/C6 matches with verified callers / total writes
    - Combined: (C2 + verified C3/C6) / total writes

    The combined rate is directly comparable to the baseline study's
    combined conditional rate (intra + inter).
    """
    print("\n" + "=" * 70)
    print(" DEVELOPER NPE PATCH ANALYSIS")
    print("=" * 70)
    total_unmatched = agg.get("total_unmatched", 0)
    print(f"  Total NPE-related patches analyzed:  {agg['total_patches']:,}")
    print(f"  Unmatched commits (check not found): {total_unmatched:,}")
    print(f"  Total writes in developer guard scopes: {agg['total_writes']:,}")
    print()
    print(f"  ── Conditional-feeding writes (all in units of write locations) ──")
    print(f"  Intra-procedural (C2, same function):  {agg['total_cond']:,}  "
          f"({agg['aggregate_cond_rate']*100:.1f}%)")
    print(f"  Inter-procedural (C3/C6 with callers): {agg['total_ip_writes']:,}  "
          f"({agg['total_ip_writes']/agg['total_writes']*100:.1f}%)"
          if agg['total_writes'] > 0 else "")
    print(f"  Combined:                              {agg['total_combined_cond']:,}  "
          f"({agg['aggregate_combined_rate']*100:.1f}%)")
    print()
    print(f"  ── Comparison with baseline study ──")
    print(f"                              Baseline  Dev patches")
    print(f"  Intra-proc conditional:     "
          f"{baseline_intra_rate*100:5.1f}%     "
          f"{agg['aggregate_cond_rate']*100:5.1f}%")
    print(f"  Combined (intra+inter):     "
          f"{baseline_combined_rate*100:5.1f}%     "
          f"{agg['aggregate_combined_rate']*100:5.1f}%")
    delta = agg['aggregate_combined_rate'] - baseline_combined_rate
    if abs(delta) < 0.03:
        print(f"  Delta: {delta*100:+.1f}pp — comparable rates.")
    elif delta > 0:
        print(f"  Delta: {delta*100:+.1f}pp — developer patches enclose more.")
        print(f"  NilGuard's minimal patches are at least as safe.")
    else:
        print(f"  Delta: {delta*100:+.1f}pp — developer patches are tighter.")

    print()
    print("  ── Per-project breakdown ──")
    print(f"    {'Project':20s} {'Ptch':>4s} {'Unm':>4s} "
          f"{'Wrt':>5s} {'Intra':>5s} {'Inter':>5s} {'Comb':>5s} {'Rate':>5s}")
    print("    " + "-" * 58)
    for p in sorted(agg["per_project"], key=lambda x: x["project"]):
        if p["patches"] == 0:
            continue
        comb_rate = p.get("combined_rate", 0) * 100
        print(f"    {p['project']:20s} {p['patches']:4d} "
              f"{p.get('unmatched', 0):4d} "
              f"{p['writes']:5d} {p['cond']:5d} "
              f"{p.get('ip_writes', 0):5d} "
              f"{p.get('combined_cond', 0):5d} {comb_rate:4.1f}%")
    print()
    print("  Columns: Ptch=patches with null checks, Unm=unmatched commits,")
    print("  Wrt=writes in guard scope, Intra=C2 cond writes,")
    print("  Inter=C3/C6 writes with verified callers, Comb=Intra+Inter")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Mine developer NPE patches and compare criticality"
    )
    parser.add_argument("--dataset", default="./dataset")
    parser.add_argument("--output", default="./results/dev_patches")
    parser.add_argument("--rules", default="rules.yaml",
                        help="Semgrep rules file (same as baseline study)")
    parser.add_argument("--project", default=None)
    parser.add_argument("--exclude", default="",
                        help="Comma-separated projects to exclude")
    parser.add_argument("--reprocess", action="store_true",
                        help="Recompute aggregates from cached results")
    parser.add_argument("--joern-dir", default="./results/joern",
                        help="Directory with Joern interproc results "
                             "(from main study, for cross-reference)")
    parser.add_argument("--baseline-cond-rate", type=float, default=0.086,
                        help="Baseline intra-proc conditional rate from main study")
    parser.add_argument("--validate", type=int, default=0, metavar="N",
                        help="Randomly sample N patches for manual inspection")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    exclude_set = set(x.strip() for x in args.exclude.split(",") if x.strip())
    exclude_set.update(SKIP_PROJECTS)

    joern_dir = args.joern_dir if os.path.isdir(args.joern_dir) else None
    if joern_dir:
        print(f"Joern cross-reference: {joern_dir}")
    else:
        print("No Joern data found — inter-procedural cross-reference disabled")

    all_results = []

    if args.reprocess:
        for f in sorted(Path(args.output).glob("*_patches.json")):
            name = f.stem.replace("_patches", "")
            if args.project and name != args.project:
                continue
            if name in exclude_set:
                continue
            with open(f) as fh:
                data = json.load(fh)
            n = len(data.get("patches", []))
            print(f"  [{name}] cached: {n} patches")
            all_results.append(data)
    else:
        dataset_path = Path(args.dataset)
        if args.project:
            projects = [dataset_path / args.project]
        else:
            projects = sorted([
                p for p in dataset_path.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ])

        for proj_dir in projects:
            name = proj_dir.name
            if name in exclude_set:
                print(f"\n[{name}] EXCLUDED")
                continue

            result = process_project(
                str(proj_dir), name, args.rules, args.output,
                joern_dir=joern_dir,
            )
            all_results.append(result)

    if not all_results:
        print("No results to process.")
        sys.exit(1)

    # Compute and display aggregates
    agg = compute_aggregate(all_results)
    print_summary(agg,
                  baseline_combined_rate=0.23,
                  baseline_intra_rate=args.baseline_cond_rate)

    # Save aggregate stats
    stats_path = os.path.join(args.output, "dev_patch_stats.json")
    stats_save = {k: v for k, v in agg.items() if k != "per_project"}
    with open(stats_path, "w") as f:
        json.dump(stats_save, f, indent=2)
    print(f"\nStats written to {stats_path}")

    # Save per-project CSV
    csv_path = os.path.join(args.output, "dev_patch_data.csv")
    fieldnames = ["project", "npe_commits", "patches", "null_checks_found",
                  "unmatched", "writes", "critical", "cond", "ip_writes",
                  "combined_cond", "crit_rate", "cond_rate", "combined_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for p in agg["per_project"]:
            writer.writerow(p)
    print(f"CSV written to {csv_path}")

    # Validation sample: randomly pick N patches for manual inspection
    if args.validate > 0:
        import random
        all_patches = []
        for proj in all_results:
            for patch in proj["patches"]:
                patch["_project"] = proj["project"]
                all_patches.append(patch)

        sample = random.sample(all_patches,
                               min(args.validate, len(all_patches)))

        val_path = os.path.join(args.output, "validation_sample.txt")
        with open(val_path, "w") as f:
            f.write(f"# Validation sample: {len(sample)} patches\n")
            f.write(f"# For each patch, manually verify:\n")
            f.write(f"#   1. Is the null check a genuine NPE fix?\n")
            f.write(f"#   2. Does the extracted scope match the "
                    f"developer's guard?\n")
            f.write(f"#   3. Are the writes correctly classified?\n")
            f.write(f"# Mark each as AGREE / DISAGREE / UNCERTAIN\n\n")

            for i, patch in enumerate(sample, 1):
                f.write(f"{'='*60}\n")
                f.write(f"SAMPLE {i}/{len(sample)}  "
                        f"Project: {patch['_project']}  "
                        f"Commit: {patch['commit']}\n")
                f.write(f"Subject: {patch['subject']}\n")
                f.write(f"Writes: {patch['total_writes']}  "
                        f"Cond: {patch['cond_writes']}  "
                        f"IP1: {patch.get('ip1_flows', 0)}  "
                        f"IP2: {patch.get('ip2_flows', 0)}\n")
                f.write(f"Rate: {patch['cond_rate']*100:.1f}%\n\n")

                for filepath, details in patch.get("file_details", {}).items():
                    f.write(f"  File: {filepath}\n")
                    for j, (text, ctx) in enumerate(zip(
                            details.get("check_texts", []),
                            details.get("diff_contexts", []))):
                        scope = details.get("scope_info", [{}])
                        si = scope[j] if j < len(scope) else {}
                        f.write(f"  Check: {text}\n")
                        f.write(f"  Scope: L{si.get('scope_start','?')}"
                                f"-L{si.get('scope_end','?')} "
                                f"({si.get('scope_size','?')} lines)\n")
                        if ctx:
                            f.write(f"  Diff context:\n")
                            for cl in ctx.split("\n"):
                                f.write(f"    {cl}\n")
                    f.write("\n")
                f.write(f"VERDICT: [AGREE / DISAGREE / UNCERTAIN]\n")
                f.write(f"NOTES:\n\n")

        print(f"Validation sample written to {val_path}")


if __name__ == "__main__":
    main()
