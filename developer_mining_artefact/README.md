# NilGuard Empirical Study: Write Criticality Near Pointer Dereferences

## Research Question

When NilGuard applies a **Skip patch** — `if (ptr != NULL) { ...scope... }` — to guard a null-pointer dereference, it conditionalizes all writes inside that scope. If `ptr` is null at runtime, those writes are skipped entirely.

**How often are those skipped writes semantically critical?** That is, how often would skipping them break program behavior observable outside the patched scope?

If the rate is low, NilGuard's *local safety* (per-path crash elimination) is a good proxy for *global safety* (no new bugs introduced). If the rate is high, formal global-safety analysis is needed.

## Pipeline Overview

The analysis has two stages, each using a different tool for a different scope:

```
┌─────────────────────────────────────────────────────────┐
│  Stage 1: Semgrep (intra-procedural, intra-file)        │
│                                                         │
│  For each function:                                     │
│    1. Find pointer dereferences (D-rules)               │
│    2. Find writes in the same scope (B-rules)           │
│    3. Classify writes as critical (C-rules)             │
│    4. Build per-location category membership            │
│    5. Compute overlap matrix + deduplicated counts      │
│                                                         │
│  Output: per-project criticality rate,                  │
│          overlap matrix, compound flow counts            │
├─────────────────────────────────────────────────────────┤
│  Stage 2: Joern (inter-procedural, cross-file)          │
│                                                         │
│  Starting from semgrep's C3/C6 matches only:            │
│    1. Resolve (file, line) → enclosing function name    │
│    2. IP1: Does any caller assign the return value      │
│       and then use it in a conditional?                 │
│    3. IP2: Does any caller pass a pointer argument      │
│       and then check it in a conditional?               │
│                                                         │
│  Output: IP1 + IP2 counts per project,                  │
│          extended criticality rate                       │
└─────────────────────────────────────────────────────────┘
```

## Stage 1: Semgrep — Intra-Procedural Analysis

### What "near a dereference" means

Semgrep's `...` (ellipsis) operator matches any sequence of statements within the **same lexical block**. When we write:

```yaml
pattern: |
  $X = $PTR->$F;
  ...
  $VAR = $EXPR;
```

this matches a pointer dereference (`$PTR->$F`) followed by an assignment (`$VAR = $EXPR`) **within the same function, in the same block scope**. The `...` can span zero or more statements, but cannot cross function boundaries or enter/exit nested blocks.

This corresponds to the scope that a NilGuard Skip patch would guard: `LScope(C)` — the lexical scope of the faulting statement.

### What we count

#### Dereferences (D-rules: D1–D4)

These identify all pointer dereference sites, providing the normalization denominator.

| Rule | Pattern | Example |
|------|---------|---------|
| D1 | Arrow operator: `$PTR->$F` | `x = node->val;` or `node->next = p;` |
| D2 | Star operator: `*$PTR` | `x = *ptr;` or `*ptr = 42;` |
| D3 | Array subscript: `$PTR[$IDX]` | `arr[i] = 0;` |
| D4 | Free: `free($PTR)` | `free(node);` |

Any of these could be the site of an NPE that NilGuard patches.

#### Baseline writes (B-rules: B1–B7)

These count **all** assignments co-located with a dereference in the same scope — every write that a Skip patch *could* enclose. This is the denominator for the criticality rate.

| Rule | Pattern | Description |
|------|---------|-------------|
| B1 | `$X = $PTR->$F; ... $VAR = $EXPR;` | Write after arrow-read dereference |
| B2 | `$PTR->$F = $E; ... $VAR = $EXPR;` | Write after arrow-write dereference |
| B3 | `$X = *$PTR; ... $VAR = $EXPR;` | Write after star-read dereference |
| B4 | `*$PTR = $E; ... $VAR = $EXPR;` | Write after star-write dereference |
| B5 | `free($PTR); ... $VAR = $EXPR;` | Write after free |
| B6 | `$VAR = $EXPR; ... $PTR->$F = $E;` | Write before arrow dereference |
| B7 | `$VAR = $EXPR; ... *$PTR = $E;` | Write before star dereference |

#### Critical writes (C-rules: C1–C8)

These classify writes as semantically critical — writes whose conditionalization (skipping on the null path) could break behavior outside the patched scope.

| Category | Rules | Pattern | Why it's critical |
|----------|-------|---------|-------------------|
| **Heap write** | C1a–c, C6 | `$PTR->$F = $E; ... *$PTR2 = $EXPR;` | Modifies shared mutable state visible to callers/aliases. If skipped, callers see stale data. |
| **Conditional-feeding** | C2a–c | `$X = $PTR->$F; ... $VAR = $EXPR; ... if (<...$VAR...>) {...}` | The written variable determines downstream control flow. Skipping it means a different branch is taken. |
| **Return-feeding** | C3a–b | `$X = $PTR->$F; ... $VAR = $EXPR; ... return $VAR;` | The value leaves the function. Caller behavior depends on it. |
| **Call-escaping** | C4a–b | `$X = $PTR->$F; ... $VAR = $EXPR; ... $FUNC(...,$VAR,...);` | The value is passed to another function. Over-approximation: callee may or may not use it critically. |
| **Persistence/I/O** | C5a–b | `$X = $PTR->$F; ... $VAR = $EXPR; ... fprintf(...,$VAR,...);` | The value affects external state (files, logs, network). |
| **Resource mgmt** | C7a–b, C8 | `$X = $PTR->$F; ... $VAR = malloc(...);` or `free($VAR);` | Skipping malloc → use-after-uninit downstream. Skipping free → memory leak. |

### How deduplication works

A single write at `(file, line)` can match multiple C-rules simultaneously. For example, `result = ptr->val;` followed by `if (result > 0) { ... }` followed by `return result;` matches C2 (conditional-feeding) AND C3 (return-feeding). Without deduplication, this one write is counted twice.

**Per-location category tagging:**

```
For each semgrep finding:
    key = (file, start_line)
    rule_id → category short name (heap, cond, ret, call, io, rsrc)
    location_cats[key].add(category)

# Example: a write at main.c:42 matches both C2a and C3a
# location_cats[("main.c", 42)] = {"cond", "ret"}
```

**Deduplicated count** = number of unique `(file, line)` keys in `location_cats`. This is the TRUE number of critical write locations, used as the primary numerator for the criticality rate.

**Raw count** = sum of per-category counts (inflated by double-counting). Reported for transparency but NOT used for rates.

### The overlap matrix

The overlap matrix is an N×N table where cell `(A, B)` counts how many unique locations belong to BOTH category A and category B. This serves two purposes:

1. **Quantifies double-counting**: `raw_sum - dedup_total = double_counted_locations`

2. **Replaces compound semgrep rules**: Instead of writing a three-stage semgrep pattern (`deref ... heap_write ... conditional`) which times out on large codebases due to O(n³) matching cost, we compute the same information via post-processing. `overlap[(heap, cond)] = 6,848` means "6,848 locations are BOTH a heap write AND feed a conditional" — exactly what a compound rule would detect.

Example overlap matrix:

```
         Call   Cond   Heap    I/O    Ret   Rsrc
  Call      ·   9133   5894    215   3044   1454
  Cond   9133      ·   6848    179   3567   1488
  Heap   5894   6848      ·     86   2131   1078
   I/O    215    179     86      ·     26     63
   Ret   3044   3567   2131     26      ·    411
  Rsrc   1454   1488   1078     63    411      ·
```

Reading: 6,848 write locations are classified as both a heap write (C1/C6) and a conditional-feeding write (C2). These are the most dangerous writes for a Skip patch to enclose — the write mutates shared state AND the conditional logic depends on it.

### The criticality rate

```
criticality_rate = deduplicated_critical_locations / total_baseline_writes
```

This is the PRIMARY metric. It answers: "What fraction of writes near dereferences are semantically critical?"

## Stage 2: Joern — Inter-Procedural Analysis

### Why we need a second tool

Semgrep Community Edition is **intra-procedural and intra-file only**. It can detect that a write feeds a `return` statement (C3), but it CANNOT detect whether the caller checks that return value in a conditional. Similarly, it can detect a write through an output parameter (C6), but not whether the caller branches on the mutated value.

These two cross-function flows are invisible to semgrep:

```c
// FILE: util.c
int parse_config(Config *cfg) {
    int flags = cfg->flags;     // ← dereference
    int result = flags & 0x01;  // ← write (C3: feeds return)
    return result;              // semgrep sees this
}

// FILE: main.c
void init() {
    int ok = parse_config(cfg); // ← semgrep can't see this connection
    if (ok) { ... }             // ← nor this conditional use
}
```

### What Joern adds

Joern builds a **Code Property Graph (CPG)** — a combined AST + control flow + data flow graph — from the raw source files (no compilation needed). It can query across function and file boundaries.

We use Joern for exactly **two** inter-procedural queries, both scoped to locations that semgrep already identified as critical:

#### IP1: Return value → caller conditional

**Starting point:** Semgrep C3 matches (writes that feed a `return` near a dereference).

**What Joern checks:** For each function containing a C3 match, find all call sites across the codebase. At each call site, check:
- **Case A (direct):** Is the call directly inside a condition? `if (func(...)) { ... }`
- **Case B (assigned):** Is the return value assigned to a variable that later appears in a condition? `int ret = func(...); ... if (ret > 0) { ... }`

**Joern query structure (Case B):**

```scala
// 1. Find all assignments whose RHS calls a target function
val targetAssigns = cpg.call.name("<operator>.assignment")
  .where(_.argument(2).ast.isCall.filter(c => targetFuncNames.contains(c.name)))

// 2. For each, get the LHS variable name
val ident = asgn.argument(1).code  // e.g., "ret"

// 3. Check if that variable appears in a conditional in the same caller
callerMethod.ast.isControlStructure
  .where(_.condition.ast.isIdentifier.name(ident))
```

**Example detected flow:**
```
callee: parse_config (util.c:12)  →  caller: init (main.c:45)
  callLine=45  condLine=46  variable=ok  type=IP1
```

#### IP2: Output parameter → caller conditional

**Starting point:** Semgrep C6 matches (writes through output pointer parameters near a dereference).

**What Joern checks:** For each function containing a C6 match, find call sites and check:
- If the caller passes an argument by address (`&var`) and later uses `var` in a conditional
- If the caller passes a pointer that is later dereferenced AND checked in a conditional

**Example detected flow:**
```c
// callee writes through output param near a deref
void read_data(Buffer *buf, int *out_len) {
    char *p = buf->data;     // dereference
    *out_len = buf->len;     // C6: output param write
}

// caller checks the mutated value
void process(Buffer *buf) {
    int len;
    read_data(buf, &len);    // IP2: &len passed
    if (len > 0) { ... }     // len checked in conditional
}
```

### Why only these two queries

The other critical categories do NOT need inter-procedural tracking:

| Category | Why intra-procedural is sufficient |
|----------|------------------------------------|
| **Heap write (C1)** | The write itself is critical regardless of who reads the heap elsewhere. The overlap matrix already counts heap∩cond for intra-procedural impact. |
| **Call-escaping (C4)** | Already captures the act of passing data out — what the callee does is beyond any static tool's practical scope. |
| **Persistence/I/O (C5)** | The I/O call itself is the observable effect — already counted. |
| **Resource mgmt (C7/C8)** | malloc/free effects are inherently local to the allocation site. |

IP1 and IP2 are the ONLY flows where data silently crosses a function boundary: the value *leaves* the function (via return or out-param) and the *caller* makes a control-flow decision based on it.

### How scoping works (semgrep → Joern)

Joern does NOT independently scan for dereferences. It only analyzes functions that **semgrep already identified** as containing critical writes near dereferences:

```
semgrep C3 matches → extract (file, line) pairs
  → Joern resolves enclosing function names
    → Joern checks callers of THOSE functions only
```

This prevents inflation: without scoping, Joern would count every cross-function conditional flow in the entire codebase (300K+ for a large project). With scoping, it counts only flows rooted in NPE-relevant locations (typically hundreds to low thousands).

### How function resolution works

Semgrep reports `(file, line)` but not function names. Joern resolves them:

```scala
// Group targets by filename for indexed lookup
for ((fileSuffix, targetLines) <- targetByFile) {
    // Fast: Joern indexes filenames
    val methodsInFile = cpg.method.where(_.filename(".*" + Pattern.quote(fileSuffix)))

    // Only check line numbers for methods in matching files
    for (m <- methodsInFile) {
        if (targetLines.exists(l => m.ast.lineNumber.toSet.contains(l))) {
            targetFuncNames += m.name
        }
    }
}
```

This is optimized for large CPGs: filename filtering is indexed (fast), and line-number checking only runs on methods in matching files (few per file).

## Combined Metric: Total Writes Flowing to a Conditional

The paper's central argument is about writes flowing into conditionals — these are the writes most likely to cause behavioral divergence if conditionalized by a Skip patch.

The **total conditional impact** combines both stages:

```
Total = intra_cond + ip_write_locations

Where:
  intra_cond         = unique (file, line) locations tagged as "cond"
                       (from semgrep overlap_per_cat["cond"])

  ip_write_locations = unique (file, line) C3/C6 write locations whose
                       enclosing function has verified Joern callers
                       (NOT flow tuples — write locations, same unit as intra)

Total conditional rate = (intra_cond + ip_write_locations) / total_baseline_writes
```

### Ensuring comparable units across both studies

Both the baseline and the developer patch study count the same thing: unique write locations identified by (file, line) pairs. For inter-procedural impact, both use identical logic:

1. Find C3 (return-feeding) or C6 (output-parameter) write locations from semgrep
2. Resolve each to its enclosing function name using the same backward-scanning regex heuristic (`extract_function_name_from_check`)
3. Check whether that function has callers that test the return value or mutated argument in a conditional (from Joern's IP1/IP2 callee sets)
4. Count the write location if the function has verified callers

This avoids the unit mismatch that would arise from counting Joern flow tuples (callee × caller × conditional). A function with 3 C3 write locations and 50 callers contributes 3 to the inter-procedural count, not 50.

The only difference between the two studies is the scope:
- **Baseline**: semgrep's `...` operator (same lexical block as the dereference = LScope(C))
- **Developer patches**: tree-sitter extraction of the developer's actual guard block

### Baseline results

| Metric | Count (%) |
|--------|-----------|
| Total baseline writes near dereferences | 154,253 |
| Intra-procedural conditional (C2, same function) | 13,315 (8.6%) |
| Inter-procedural conditional (C3/C6 with verified callers) | 2,879 (1.9%) |
| Combined | 16,194 (10.5%) |

### What `intra_cond` includes

`intra_cond` counts every unique write location in the "cond" category. Many of these locations also belong to other categories. The overlap matrix tells us how many of the cond locations also have another critical property, but these are NOT additive — a single location can be tagged as heap AND call AND cond simultaneously.

| Metric | What it means |
|--------|---------------|
| `intra_cond` = 13,315 | Total unique intra-procedural locations feeding a conditional |
| `overlap(heap, cond)` = 6,324 | Of those 13,315, this many are ALSO heap writes |
| `overlap(call, cond)` = 8,144 | Of those 13,315, this many ALSO escape via a function call |
| `overlap(rsrc, cond)` = 1,485 | Of those 13,315, this many ALSO involve malloc/free |

### Why only conditionals?

A write that feeds a conditional is the most dangerous write for a Skip patch to enclose because:
1. Skipping the write changes the condition's evaluation → a different branch is taken
2. The different branch may execute code that the original program never reached
3. This is observable: tests fail, behavior changes, new crashes

Other critical writes (heap mutations, I/O) are also dangerous but their impact is harder to measure without full semantic analysis. Conditional-feeding writes have a direct, observable control-flow consequence.

### How we avoid double-counting

Double-counting inflates the criticality rate and makes the results unreliable. We handle it at three levels:

#### 1. Within a single category (per-rule deduplication)

A single semgrep rule can match the same `(file, line)` location via different sub-patterns in a `pattern-either` block. We deduplicate per rule:

```python
if key not in seen[rule_id]:
    seen[rule_id].add(key)
    counts[rule_id] += 1
```

Each rule counts each location at most once.

#### 2. Across categories (the overlap matrix)

A single write can match multiple C-rules. For example, `result = ptr->val; if (result) {...} return result;` matches C2 (conditional-feeding), C3 (return-feeding), and C4 (call-escaping if passed to a function). Without deduplication, this one write would be counted three times.

We tag each `(file, line)` with the SET of categories it belongs to:

```python
# location_cats[("main.c", 42)] = {"cond", "ret", "call"}
# This is ONE location, not three.
```

The **deduplicated count** = number of unique keys in `location_cats`. This is the numerator for the primary criticality rate. The **raw count** (sum of per-category counts) is reported for transparency but NOT used for rates.

The overlap matrix quantifies exactly how much double-counting exists:
```
raw_sum_of_categories   = 59,347  (inflated)
unique_critical_locations = 27,350  (deduplicated)
double_counted           = 31,997  (53.9% inflation)
```

#### 3. Between intra-procedural and inter-procedural counts

The intra-procedural counts (semgrep) and inter-procedural counts (Joern) measure **different locations at different sites** and are inherently disjoint:

| Metric | What location it counts | Where in the code |
|--------|------------------------|-------------------|
| `intra_cond` | The write itself (e.g., line where `result = expr;` appears) | Inside the function with the dereference (the callee) |
| IP write locations | C3/C6 write locations whose enclosing function has verified callers | Inside the callee, same as intra — but verified to have cross-function impact |

The inter-procedural count is a SUBSET of the C3/C6 write locations that semgrep already found. It does not add new locations — it verifies that certain return-feeding or output-parameter writes actually have callers that check the value. This means intra_cond and ip_write_locations can overlap if a write feeds both a local conditional (C2) AND a return that a caller checks (C3 + IP1 callers). In practice this overlap is small and we accept the slight over-count.

Both the baseline and dev patch studies use identical logic for this counting:
1. Same semgrep rules identify C3/C6 locations
2. Same `extract_function_name_from_check` heuristic resolves function names
3. Same Joern callee sets determine which functions have verified callers
4. Same unit: unique (file, line) write locations

#### Summary of deduplication guarantees

| Level | Mechanism | What it prevents |
|-------|-----------|------------------|
| Per-rule | `seen[rule_id]` set per `(file, line)` | Same rule matching same location twice |
| Cross-category | `location_cats` dict keyed by `(file, line)` | Same location inflating the count via multiple C-rules |
| Cross-tool | Inherently disjoint locations (callee vs caller) | Semgrep and Joern counting the same write twice |
| Within Joern | Dedup by `(callerFile, callLine, condLine, type)` | Same Joern flow counted twice |

## Scope and Limitations

### Semgrep Community Edition

| Property | Value |
|----------|-------|
| Intra-procedural |  Within one function |
| Inter-procedural |  Cannot track across functions |
| Inter-file | Cannot track across files |
| Alias tracking | No pointer aliasing |
| `...` operator | Matches within same lexical block |

**Over-approximation bias**: C4 (call-escaping) flags ANY function call. The callee may ignore the argument. A write and a conditional in the same scope may not have a true data dependency.

**Under-approximation bias**: Misses aliased writes (`*q` where `q` aliases `p`), misses inter-procedural flows (handled by Joern), misses writes in nested scopes.

### Joern

| Property | Value |
|----------|-------|
| Inter-procedural | Cross-function, cross-file |
| CPG construction | Fuzzy parser, no build system needed |
| Scale limit | ~100MB CPG / ~500K AST nodes in batch mode |
| Alias tracking |  Not used in our queries |

**Limitation**: CPython (110MB CPG) exceeds Joern's batch-mode capacity and is excluded from inter-procedural results.

**Over-approximation**: A variable used in a conditional after a call doesn't guarantee data dependence (it may have been redefined between the call and the conditional).

### Net effect on the criticality rate

The intra-procedural rate is an **upper bound** within a function (C4 is very broad) and a **lower bound** across functions (misses IP1/IP2). Adding Joern's inter-procedural counts corrects the cross-function under-approximation. The combined rate is the most accurate estimate this toolchain can provide.

## Reproducing the Study

### Prerequisites

```bash
pip install semgrep                    # v1.x
# Joern: https://docs.joern.io/installation/
# JDK 19+, 8GB+ RAM
```

### Step 1: Clone the dataset (once)

```bash
bash setup_dataset.sh ./dataset
```

### Step 2: Semgrep intra-procedural analysis

```bash
# Full run (scans all projects, ~30 min)
python3 analyze_local_safety.py --rules rules.yaml --dataset ./dataset --output ./results

# Re-generate tables from cached results (instant)
python3 analyze_local_safety.py --reprocess --output ./results
```

### Step 3: Joern inter-procedural analysis

```bash
# Full run (builds CPGs + runs queries, ~2 hours)
python3 joern_interproc.py --dataset ./dataset --output ./results --max-memory 12g

# Single project (for debugging)
python3 joern_interproc.py --dataset ./dataset --output ./results --project busybox

# Re-merge from cached Joern results (instant)
python3 joern_interproc.py --reprocess --output ./results
```

### Outputs

| File | Description |
|------|-------------|
| `results/stats.json` | Aggregate semgrep statistics |
| `results/criticality_data.csv` | Per-project semgrep data (for R/pandas) |
| `results/table_criticality.tex` | LaTeX summary table |
| `results/table_breakdown.tex` | LaTeX per-project sub-category breakdown |
| `results/stats_extended.json` | Combined semgrep + Joern statistics |
| `results/criticality_extended.csv` | Per-project combined data |
| `results/{project}_raw.json` | Cached semgrep JSON per project |
| `results/joern/{project}_interproc.json` | Cached Joern results per project |
| `results/joern/workspace/{project}.bin` | Cached Joern CPGs (reusable) |

### Incremental re-analysis

When only the reporting logic changes (not the rules), use `--reprocess` to regenerate all outputs from cached results without re-running semgrep or Joern. A full re-scan is only necessary when the semgrep rules or Joern queries change.

## File Inventory

| File | Purpose |
|------|---------|
| `rules.yaml` | Semgrep rules: D1–D4, B1–B7, C1–C8, N1 |
| `analyze_local_safety.py` | Semgrep runner, overlap matrix, dedup, tables |
| `joern_interproc.py` | Joern IP1/IP2 queries, merge with semgrep |
| `setup_dataset.sh` | Clone 29 C projects at pinned versions |
| `SEMGREP_SCOPE.md` | Detailed semgrep scope documentation |
| `test_patterns.c` | Synthetic test exercising all rule categories |
| `realistic_patterns.c` | Production-like code for ratio validation |
| `ip1_test.c` | Minimal test for Joern IP1 query debugging |
| `joern_debug.scala` | Standalone Joern AST dump for debugging |
| `mine_dev_patches.py` | Developer NPE patch mining and comparison |

## Developer Patch Comparison Study

The baseline study answers "how often do writes near dereferences feed conditionals?" for hypothetical patches at arbitrary dereference sites. A natural follow-up is: what happens at sites where developers actually fixed NPEs? If developer patches exhibit a similar or higher conditional-feeding rate, NilGuard's automated patches are at least as safe as human-written ones.

### Pipeline

#### Step 1: Commit discovery (bag of words)

We search each project's git history for commits whose messages match NPE-related terms:

```
git log --grep "null pointer" --grep "segfault" --grep "NPE" ... -i --diff-filter=M -- "*.c"
```

Sixteen search terms are used (case-insensitive): `null pointer`, `null dereference`, `null check`, `NPE`, `SIGSEGV`, `segfault`, `fix.*null`, `null.*crash`, and others. Each project is capped at 200 matching commits. Commits that match the search but whose diff contains no recognizable null check are stored as "unmatched" with their diff preview for manual review.

Note: the repos must have full git history. The `setup_dataset.sh` clones with `--depth 1`; run `git -C <repo> fetch --unshallow` before this step.

#### Step 2: Null check extraction (regex on diff)

For each matching commit, we parse the unified diff and look for added lines that match null-check patterns:

```
+  if (ptr == NULL) ...
+  if (!ptr) ...
+  if (ptr != NULL) { ... }
+  if (NULL == ptr) ...
```

The regex matches the six most common forms. It intentionally does not match compound checks like `if (ptr && ptr->field)` or macro-wrapped checks like `CHECK_NULL(ptr)`. Unmatched commits where the regex finds no null check are saved to `{project}_unmatched.txt` for manual inspection — these represent potential false negatives of the regex.

#### Step 3: Scope extraction (tree-sitter)

For each added null check, we extract the file at that commit (`git show commit:path`) and parse it with tree-sitter to determine the exact scope of the developer's guard. Three patterns are handled:

| Pattern | Scope |
|---------|-------|
| `if (ptr == NULL) return -1;` | From the if-statement's end to the end of the enclosing function |
| `if (ptr == NULL) goto err;` | Same as above |
| `if (ptr != NULL) { ... }` | The compound statement body |

If tree-sitter is not installed or parsing fails (e.g., due to preprocessor conditionals or macros), a fallback of ±30 lines is used. The scope extraction produces a `(start_line, end_line)` range per null check.

#### Step 4: Semgrep analysis

We run the same `rules.yaml` from the baseline study on the post-patch file. Semgrep results are filtered to only include findings within the extracted scope from Step 3. This counts writes near dereferences inside the developer's guard, and classifies them using the same C-rules (C1–C8).

The primary metric is the **intra-procedural conditional-feeding rate**: of all writes in the developer's guard scope, what fraction feeds an `if`/`while`/`for` condition within the same function? This is directly comparable to the baseline study's 8.6% intra-procedural rate.

#### Step 5: Joern cross-reference (gated)

We reuse existing Joern IP1/IP2 results from the baseline study (no additional CPG builds). For each function containing a developer's null check, we check whether that function name appears as a callee in the Joern inter-procedural flow data.

Critically, the cross-reference is **gated on the guard scope contents**: we only count IP1 flows if the developer's guard scope contains a C3 (return-feeding) semgrep match, and only count IP2 flows if it contains a C6 (output-parameter) match. Without this gate, we would count IP flows for functions where the developer's guard has nothing to do with the return path — e.g., a narrow guard `if (p != NULL) { p->val = 0; }` that doesn't affect the function's return value.

The gating logic:

```
1. Run semgrep on post-patch file → findings
2. Filter findings to guard scope → scoped findings
3. Check: does any scoped finding have rule ID starting with "C3"?
   → if yes, the guard affects a return-feeding write → IP1 flows are relevant
4. Check: does any scoped finding have rule ID starting with "C6"?
   → if yes, the guard affects an output-parameter write → IP2 flows are relevant
5. Only count Joern flows for the categories that passed the gate
```

Inter-procedural counts are reported as "N of M patches have caller-side flows" rather than as a rate. This is because IP flows are counted per (callee, caller, conditional) tuple while writes are per (file, line) location — different units that cannot be meaningfully combined into a single percentage.

### Results

Across 28 projects, we found 2,053 NPE-related commits, of which 684 contained a recognizable null check in the diff. The remaining 1,369 are stored as unmatched for potential manual review.

| Metric | Value |
|--------|-------|
| NPE-related patches analyzed | 684 |
| Unmatched commits (check not found) | 1,369 |
| Total writes in developer guard scopes | 6,912 |
| Intra-proc conditional writes (C2) | 738 (10.7%) |
| Inter-proc conditional writes (C3/C6 with callers) | 99 (1.4%) |
| Combined conditional writes | 837 (12.1%) |

The comparison with the baseline study:

|  | Baseline (all dereferences) | Developer NPE patches |
|--|---|---|
| Intra-procedural conditional | 8.6% | 10.7% |
| Inter-procedural conditional | 1.9% | 1.4% |
| Combined (intra + inter) | 10.5% | 12.1% |
| Delta | | +1.6pp |

Developer patches have a combined conditional rate of **12.1%**, compared to the baseline's **10.5%** — a difference of +1.6 percentage points. The intra-procedural rates are similar (10.7% vs 8.6%), and the inter-procedural rates are both small (1.4% vs 1.9%). The slightly higher intra-procedural rate for developer patches is consistent with the observation that developers fix real NPEs in code with genuine null-dependent control flow, while the baseline includes many dereferences in simple accessor functions.

Several projects show 0% conditional rates despite having writes in the guard scope (libpng: 119 writes, lua: 339 writes, tmux: 45 writes). In these cases, the developer's null check encloses only writes to heap fields, local variables, and I/O calls — none of which feed downstream conditionals. These are precisely the "safe" writes that a locally safe patch can enclose without risk.

The key takeaway: NilGuard's automated patches, which target the minimal scope around a confirmed dereference error, produce guard scopes whose conditional-feeding profile is comparable to human-written NPE patches. Both developer and automated patches enclose predominantly non-conditional writes, supporting the claim that local safety is a practical criterion for NPE patch generation.

### Output files

| File | Description |
|------|-------------|
| `{project}_patches.json` | Full results per project (cached) |
| `{project}_null_checks.txt` | Human-readable manifest of every null check found |
| `{project}_unmatched.txt` | Commits that matched NPE search but had no detectable null check |
| `dev_patch_stats.json` | Aggregate statistics |
| `dev_patch_data.csv` | Per-project data for R/pandas |
| `validation_sample.txt` | Random sample for manual validation (with `--validate N`) |
