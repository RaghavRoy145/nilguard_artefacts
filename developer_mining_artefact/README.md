# NilGuard Empirical Study: Write Criticality Near Pointer Dereferences

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

### Baseline results

| Metric | Count (%) |
|--------|-----------|
| Total baseline writes near dereferences | 154,253 |
| Intra-procedural conditional (C2, same function) | 13,315 (8.6%) |
| Inter-procedural conditional (C3/C6 with verified callers) | 2,879 (1.9%) |
| Combined | 16,194 (10.5%) |

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
# Full run (builds CPGs + runs queries, ~2 hours) may require setting the joern bin as an ENV Var!
python3 joern_interproc.py --dataset ./dataset --output ./results --max-memory 12g

# Single project (for debugging)
python3 joern_interproc.py --dataset ./dataset --output ./results --project busybox

# Re-merge from cached Joern results (requires --dataset for write-location counting)
python3 joern_interproc.py --reprocess --output ./results --dataset ./dataset --exclude cpython
```

### Step 4: Developer patch mining

Requires full git history (not shallow clones):

```bash
# Unshallow repos (one-time, may take a while)
for repo in dataset/*/; do git -C "$repo" fetch --unshallow 2>/dev/null; done

# Full run (~1 hour, runs semgrep per commit)
python3 mine_dev_patches.py --dataset ./dataset --output ./results/dev_patches --exclude cpython

# Single project
python3 mine_dev_patches.py --dataset ./dataset --output ./results/dev_patches --project redis

# Re-aggregate from cached results (instant)
python3 mine_dev_patches.py --reprocess --output ./results/dev_patches --exclude cpython

# Generate validation sample for manual inspection
python3 mine_dev_patches.py --reprocess --output ./results/dev_patches --exclude cpython --validate 50
```

### Step 5: Statistical analysis

Runs after both studies are complete. Tests whether local safety correlates with global safety using Fisher's exact test and phi coefficient.

```bash
pip install scipy

# Dev patches only (fast, no dataset access needed)
python3 stat_analysis.py --dev-results ./results/dev_patches --skip-baseline

# Full analysis including baseline (needs dataset for function-name resolution)
python3 stat_analysis.py --dev-results ./results/dev_patches --baseline-results ./results --dataset ./dataset
```

### Incremental re-analysis

When only the reporting logic changes (not the rules), use `--reprocess` to regenerate all outputs from cached results without re-running semgrep or Joern. A full re-scan is only necessary when the semgrep rules or Joern queries change.


## Developer Patch Comparison Study

The baseline study answers "how often do writes near dereferences feed conditionals?" for hypothetical patches at arbitrary dereference sites. A natural follow-up is: what happens at sites where developers actually fixed NPEs? If developer patches exhibit a similar or higher conditional-feeding rate, NilGuard's automated patches are at least as safe as human-written ones.

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

======================================================================
 STATISTICAL ANALYSIS: LOCAL SAFETY ↔ GLOBAL SAFETY CORRELATION
======================================================================

  Definitions:
    Locally safe  = guard scope has NO writes feeding a
                    conditional in the same function
    Globally safe = guard scope has NO writes with verified
                    inter-procedural conditional impact

  H0: Local safety and global safety are independent
  H1: Local safety and global safety are correlated

======================================================================
 DEVELOPER PATCHES (per-patch, n=684)
======================================================================

  Patches analysed: 684

  ── Developer patches: local ↔ global safety ──
                             Globally Safe  Globally Unsafe   Total
  Locally Safe                         629                4     633
  Locally Unsafe                        43                8      51
  Total                                672               12     684

  P(locally safe)  = 92.5%
  P(globally safe) = 98.2%
  P(both safe)     = 92.0%
  P(globally safe | locally safe) = 99.4%
  P(globally safe | locally unsafe) = 84.3%

  Phi coefficient: 0.3012
    → Medium association
    Direction: POSITIVE — local safety predicts global safety

  Chi-square: χ² = 62.0589, df = 1, p = 3.33e-15
    WARNING: min expected count = 0.9 < 5, prefer Fisher's exact
  Fisher's exact: OR = 29.2558, p = 2.19e-07

  Result: Highly significant (p < 0.001)

======================================================================
 BASELINE STUDY (per-write-location)
======================================================================

  Write locations classified: 24,809

  ── Baseline: local ↔ global safety (per location) ──
                             Globally Safe  Globally Unsafe   Total
  Locally Safe                      10,389            1,105  11,494
  Locally Unsafe                    11,755            1,560  13,315
  Total                             22,144            2,665  24,809

  P(locally safe)  = 46.3%
  P(globally safe) = 89.3%
  P(both safe)     = 41.9%
  P(globally safe | locally safe) = 90.4%
  P(globally safe | locally unsafe) = 88.3%

  Phi coefficient: 0.0339
    → Negligible association
    Direction: POSITIVE — local safety predicts global safety

  Chi-square: χ² = 28.4379, df = 1, p = 9.68e-08
  Fisher's exact: OR = 1.2477, p = 9.91e-08

  Result: Highly significant (p < 0.001)

======================================================================