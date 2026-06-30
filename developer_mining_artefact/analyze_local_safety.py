#!/usr/bin/env python3
"""
analyze_local_safety.py — Run semgrep rules and compute criticality statistics.

This script:
  1. Runs the semgrep rule file against each project in the dataset.
  2. Parses JSON output to count baseline writes and critical writes.
  3. Computes per-project and aggregate criticality rates.
  4. Outputs a LaTeX-ready table and summary statistics.

Usage:
    python3 analyze_local_safety.py \
        --rules  rules.yaml \
        --dataset ./dataset \
        --output  ./results
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
import csv

# ── Rule ID taxonomy ──────────────────────────────────────────────────────────
# Maps each semgrep rule ID to a category for aggregation.

BASELINE_IDS = {
    "B1-write-after-arrow-read",
    "B2-write-after-arrow-write",
    "B3-write-after-star-read",
    "B4-write-after-star-write",
    "B5-write-after-free",
    "B6-write-before-arrow-deref",
    "B7-write-before-star-deref",
}

DEREF_IDS = {
    "D1-arrow-deref",
    "D2-star-deref",
    "D3-array-deref",
    "D4-free-deref",
}

CRITICAL_IDS = {
    "C1a-heap-write-after-arrow-deref",
    "C1b-struct-field-write-after-deref",
    "C1c-array-write-after-deref",
    "C2a-write-feeds-if-after-arrow-deref",
    "C2b-write-feeds-while-after-deref",
    "C2c-write-feeds-for-after-deref",
    "C3a-write-feeds-return-after-arrow-deref",
    "C3b-write-feeds-return-after-star-deref",
    "C4a-write-escapes-call-after-arrow-deref",
    "C4b-write-escapes-call-after-star-deref",
    "C5a-write-feeds-file-io",
    "C5b-write-feeds-logging",
    "C6-output-param-write-near-deref",
    "C7a-malloc-after-deref",
    "C7b-calloc-after-deref",
    "C8-free-after-deref",
}

NONCRITICAL_IDS = {
    "N1-local-decl-write-after-deref",
}

# Fine-grained critical sub-categories for breakdown table
CRITICAL_CATEGORIES = {
    "heap_write":       {"C1a-heap-write-after-arrow-deref",
                         "C1b-struct-field-write-after-deref",
                         "C1c-array-write-after-deref",
                         "C6-output-param-write-near-deref"},
    "feeds_conditional":{"C2a-write-feeds-if-after-arrow-deref",
                         "C2b-write-feeds-while-after-deref",
                         "C2c-write-feeds-for-after-deref"},
    "feeds_return":     {"C3a-write-feeds-return-after-arrow-deref",
                         "C3b-write-feeds-return-after-star-deref"},
    "escapes_call":     {"C4a-write-escapes-call-after-arrow-deref",
                         "C4b-write-escapes-call-after-star-deref"},
    "persistence_io":   {"C5a-write-feeds-file-io",
                         "C5b-write-feeds-logging"},
    "resource_mgmt":    {"C7a-malloc-after-deref",
                         "C7b-calloc-after-deref",
                         "C8-free-after-deref"},
}

# Short names for the 6 original critical categories
CRIT_CAT_SHORT = {
    "heap_write":        "heap",
    "feeds_conditional": "cond",
    "feeds_return":      "ret",
    "escapes_call":      "call",
    "persistence_io":    "io",
    "resource_mgmt":     "rsrc",
}

# ── Compound flows (derived from overlap matrix, NOT from semgrep rules) ──
# Each entry: (source_cat, sink_cat) → label
# The overlap matrix cell overlap[(source, sink)] gives the count directly.
# This avoids expensive multi-`...` semgrep rules that time out in practice.
COMPOUND_FLOWS = {
    ("heap", "cond"):  "Heap write → conditional",
    ("rsrc", "cond"):  "Resource mgmt → conditional",
    ("ret",  "cond"):  "Return-feeding ∩ conditional",
    ("call", "cond"):  "Call-escaping ∩ conditional",
    ("heap", "ret"):   "Heap write → return",
    ("heap", "call"):  "Heap write → call escape",
    ("rsrc", "ret"):   "Resource mgmt → return",
}


def run_semgrep(rules_path: str, target_dir: str, output_path: str) -> dict:
    """Run semgrep and return parsed JSON results."""
    cmd = [
        "semgrep",
        "--config", rules_path,
        "--json",
        "--no-git-ignore",        # scan everything
        "--timeout", "300",       # 5 min per file
        "--max-target-bytes", "1000000",  # skip files > 1MB
        "--jobs", "4",
        target_dir,
    ]

    print(f"  Running semgrep on {target_dir} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if result.returncode not in (0, 1):
        # semgrep returns 1 when findings exist but no errors
        print(f"  WARNING: semgrep exited with code {result.returncode}")
        if result.stderr:
            # Print first 500 chars of stderr for debugging
            print(f"  stderr: {result.stderr[:500]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  ERROR: Could not parse semgrep JSON output")
        data = {"results": []}

    # Save raw JSON for reproducibility
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    return data


def count_findings(data: dict) -> dict:
    """Count findings by rule ID, deduplicating by (file, line) per rule.

    Builds per-location category membership for the overlap matrix.
    The overlap matrix replaces compound semgrep rules (which time out
    on large codebases) — it computes the same pairwise intersections
    via post-processing.

    Returns a dict with rule counts plus special keys:
      _unique_critical_locations: int
      _unique_baseline_locations: int
      _location_categories: dict[(file,line)] -> set of category short names
    """
    counts = defaultdict(int)
    seen = defaultdict(set)  # per-rule dedup by (file, start_line)
    critical_locations = set()
    baseline_locations = set()

    # Per-location: which critical categories does each (file, line) belong to?
    location_cats = defaultdict(set)  # (file, line) -> {cat_short, ...}

    for finding in data.get("results", []):
        rule_id = finding.get("check_id", "").split(".")[-1]
        path = finding.get("path", "")
        start_line = finding.get("start", {}).get("line", 0)
        key = (path, start_line)

        if key not in seen[rule_id]:
            seen[rule_id].add(key)
            counts[rule_id] += 1

        if rule_id in CRITICAL_IDS:
            critical_locations.add(key)
            for cat_name, rule_ids in CRITICAL_CATEGORIES.items():
                if rule_id in rule_ids:
                    location_cats[key].add(CRIT_CAT_SHORT[cat_name])

        elif rule_id in BASELINE_IDS:
            baseline_locations.add(key)

    counts["_unique_critical_locations"] = len(critical_locations)
    counts["_unique_baseline_locations"] = len(baseline_locations)
    counts["_location_categories"] = dict(location_cats)
    return dict(counts)


def compute_overlap_matrix(location_cats: dict) -> dict:
    """Compute pairwise overlap counts from per-location category sets.

    For each pair of categories (A, B), counts how many unique locations
    belong to BOTH A and B.  This tells us how much double-counting exists
    in the raw per-category totals.

    Returns:
        overlap: dict[(catA, catB)] -> int  (upper-triangle, A < B)
        per_cat: dict[cat] -> int  (unique locations per category)
        deduplicated_total: int  (unique locations in ANY category)
    """
    shorts = sorted(CRIT_CAT_SHORT.values())
    per_cat = defaultdict(int)
    overlap = defaultdict(int)

    for loc, cats in location_cats.items():
        for c in cats:
            per_cat[c] += 1
        cat_list = sorted(cats)
        for i in range(len(cat_list)):
            for j in range(i + 1, len(cat_list)):
                pair = (cat_list[i], cat_list[j])
                overlap[pair] += 1

    return {
        "overlap": dict(overlap),
        "per_cat": dict(per_cat),
        "deduplicated_total": len(location_cats),
    }


def aggregate_counts(counts: dict) -> dict:
    """Aggregate rule-level counts into category-level counts."""
    agg = {
        "total_dereferences": 0,
        "total_baseline_writes": 0,
        "total_critical_writes": 0,
        "total_noncritical_writes": 0,
    }

    # Sub-category breakdown
    for cat in CRITICAL_CATEGORIES:
        agg[f"critical_{cat}"] = 0

    for rule_id, count in counts.items():
        if rule_id.startswith("_"):
            continue  # skip metadata keys
        if rule_id in DEREF_IDS:
            agg["total_dereferences"] += count
        elif rule_id in BASELINE_IDS:
            agg["total_baseline_writes"] += count
        elif rule_id in CRITICAL_IDS:
            agg["total_critical_writes"] += count
            for cat, ids in CRITICAL_CATEGORIES.items():
                if rule_id in ids:
                    agg[f"critical_{cat}"] += count
        elif rule_id in NONCRITICAL_IDS:
            agg["total_noncritical_writes"] += count

    # Overlap matrix (pairwise dedup data) — computed in post-processing
    location_cats = counts.get("_location_categories", {})
    agg["_overlap"] = compute_overlap_matrix(location_cats)

    return agg


def compute_statistics(project_results: dict) -> dict:
    """Compute aggregate statistics across all projects.

    Two criticality rates are computed:
      - raw_criticality_rate:   sum(per-rule matches) / baseline  (inflated by double-counting)
      - criticality_rate:       unique_critical_locations / baseline  (deduplicated, PRIMARY)

    Compound flows are derived from the overlap matrix, not from semgrep rules.
    """
    totals = defaultdict(int)
    per_project_rates = []

    # Overlap aggregation across projects
    agg_overlap = {}    # (catA, catB) -> int
    agg_per_cat = {}    # cat -> int (unique locations)
    agg_dedup_total = 0

    for project, agg in project_results.items():
        for k, v in agg.items():
            if k.startswith("_"):
                continue
            totals[k] += v

        baseline = agg["total_baseline_writes"]
        raw_critical = agg["total_critical_writes"]

        # Per-project overlap data
        proj_overlap = agg.get("_overlap", {})
        dedup_critical = proj_overlap.get("deduplicated_total", raw_critical)

        # PRIMARY rate uses deduplicated count
        if baseline > 0:
            rate = dedup_critical / baseline
            raw_rate = raw_critical / baseline
        else:
            rate = 0.0
            raw_rate = 0.0

        row = {
            "project": project,
            "baseline_writes": baseline,
            "critical_writes": raw_critical,
            "dedup_critical": dedup_critical,
            "criticality_rate": rate,
            "raw_criticality_rate": raw_rate,
            "dereferences": agg["total_dereferences"],
        }

        # Per-project sub-category counts
        for cat, short in CRIT_CAT_SHORT.items():
            row[f"crit_{short}"] = agg.get(f"critical_{cat}", 0)

        # Per-project compound flows (from overlap matrix)
        proj_ov = proj_overlap.get("overlap", {})
        for (src, sink), label in COMPOUND_FLOWS.items():
            pair = tuple(sorted([src, sink]))
            row[f"flow_{src}_{sink}"] = proj_ov.get(pair, 0)

        per_project_rates.append(row)

        # Aggregate overlap matrix across projects
        for pair, cnt in proj_ov.items():
            agg_overlap[pair] = agg_overlap.get(pair, 0) + cnt
        proj_percat = proj_overlap.get("per_cat", {})
        for cat, cnt in proj_percat.items():
            agg_per_cat[cat] = agg_per_cat.get(cat, 0) + cnt
        agg_dedup_total += dedup_critical

    # Aggregate rates
    total_baseline = totals["total_baseline_writes"]
    total_critical_raw = totals["total_critical_writes"]
    raw_rate = total_critical_raw / total_baseline if total_baseline > 0 else 0
    dedup_rate = agg_dedup_total / total_baseline if total_baseline > 0 else 0

    # Per-project distribution (using deduplicated rate)
    rates = [r["criticality_rate"] for r in per_project_rates if r["baseline_writes"] > 0]
    rates.sort()
    n = len(rates)

    stats = {
        # PRIMARY metric — deduplicated
        "aggregate_criticality_rate": dedup_rate,
        "deduplicated_critical_total": agg_dedup_total,
        # Raw metric — for transparency
        "raw_criticality_rate": raw_rate,
        "total_critical_writes_raw": total_critical_raw,
        # Common
        "total_dereferences": totals["total_dereferences"],
        "total_baseline_writes": total_baseline,
        "total_noncritical_writes": totals["total_noncritical_writes"],
        "n_projects": len(per_project_rates),
        "n_projects_with_data": n,
    }

    if n > 0:
        stats["median_rate"] = rates[n // 2]
        stats["q1_rate"] = rates[n // 4]
        stats["q3_rate"] = rates[3 * n // 4]
        stats["min_rate"] = rates[0]
        stats["max_rate"] = rates[-1]
        stats["mean_rate"] = sum(rates) / n

    # Per-category breakdown (raw counts, for the per-column table)
    for cat in CRITICAL_CATEGORIES:
        key = f"critical_{cat}"
        stats[key] = totals[key]
        stats[f"{key}_pct"] = totals[key] / total_critical_raw if total_critical_raw > 0 else 0

    # Compound flows (derived from aggregated overlap matrix)
    for (src, sink), label in COMPOUND_FLOWS.items():
        pair = tuple(sorted([src, sink]))
        stats[f"compound_{src}_{sink}"] = agg_overlap.get(pair, 0)

    # Total compound flow: unique locations that appear in 2+ categories
    # where at least one is "cond"
    total_compound_cond = sum(
        agg_overlap.get(tuple(sorted([src, "cond"])), 0)
        for src in ["heap", "rsrc", "ret", "call", "io"]
    )
    stats["total_compound_to_cond"] = total_compound_cond

    # Overlap matrix (aggregated)
    stats["overlap_matrix"] = agg_overlap
    stats["overlap_per_cat"] = agg_per_cat

    stats["per_project"] = per_project_rates
    stats["cat_short"] = CRIT_CAT_SHORT
    return stats


def generate_latex_table(stats: dict, output_path: str):
    """Generate a LaTeX table for the paper."""
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Criticality of writes co-located with pointer dereferences.}",
        r"\label{tab:local-global-safety}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"\textbf{Project} & \textbf{Derefs} & \textbf{Writes} & \textbf{Critical} & \textbf{Rate (\%)} \\",
        r"\midrule",
    ]

    for p in sorted(stats["per_project"], key=lambda x: x["project"]):
        name = p["project"].replace("_", r"\_")
        rate_pct = p["criticality_rate"] * 100
        lines.append(
            f"  {name} & {p['dereferences']:,} & {p['baseline_writes']:,} "
            f"& {p['critical_writes']:,} & {rate_pct:.1f} \\\\"
        )

    lines.extend([
        r"\midrule",
        f"  \\textbf{{Total}} & {stats['total_dereferences']:,} "
        f"& {stats['total_baseline_writes']:,} "
        f"& {stats['total_critical_writes_raw']:,} "
        f"& {stats['aggregate_criticality_rate']*100:.1f} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
    ])

    # Add category breakdown as a note
    lines.append(r"\vspace{1mm}")
    lines.append(r"\footnotesize")
    cats = []
    for cat in CRITICAL_CATEGORIES:
        key = f"critical_{cat}"
        pct = stats.get(f"{key}_pct", 0) * 100
        label = cat.replace("_", " ")
        cats.append(f"{label}: {pct:.1f}\\%")
    lines.append(r"\textit{Critical breakdown}: " + ", ".join(cats) + ".")
    lines.extend([
        r"\end{table}",
    ])

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"LaTeX table written to {output_path}")


def generate_latex_breakdown_table(stats: dict, output_path: str):
    """Generate a LaTeX table showing per-project critical sub-category breakdown."""
    cat_short = stats.get("cat_short", {})
    col_labels = {"heap": "Heap", "cond": "Cond", "ret": "Ret",
                  "call": "Call", "io": "I/O", "rsrc": "Rsrc"}
    shorts = list(cat_short.values())
    n_cols = len(shorts)
    col_spec = "l" + "r" * (n_cols + 2)

    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\caption{Per-project breakdown of critical write types near pointer dereferences.}",
        r"\label{tab:critical-breakdown}",
        r"\small",
        f"\\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
    ]

    header_parts = [r"\textbf{Project}", r"\textbf{Crit.}", r"\textbf{Rate}"]
    for s in shorts:
        header_parts.append(f"\\textbf{{{col_labels.get(s, s.title())}}}")
    lines.append(" & ".join(header_parts) + r" \\")
    lines.append(r"\midrule")

    totals_row = defaultdict(int)
    for p in sorted(stats["per_project"], key=lambda x: x["project"]):
        name = p["project"].replace("_", r"\_")
        crit = p["critical_writes"]
        rate_pct = p["criticality_rate"] * 100

        parts = [name, f"{crit:,}", f"{rate_pct:.1f}\\%"]
        for s in shorts:
            val = p.get(f"crit_{s}", 0)
            totals_row[s] += val
            parts.append(str(val))
        lines.append("  " + " & ".join(parts) + r" \\")

    lines.append(r"\midrule")
    total_crit = stats.get("deduplicated_critical_total",
                           stats.get("total_critical_writes_raw", 0))
    total_rate = stats["aggregate_criticality_rate"] * 100
    total_parts = [r"\textbf{Total}", f"\\textbf{{{total_crit:,}}}",
                   f"\\textbf{{{total_rate:.1f}\\%}}"]
    for s in shorts:
        total_parts.append(f"\\textbf{{{totals_row[s]:,}}}")
    lines.append("  " + " & ".join(total_parts) + r" \\")

    pct_parts = [r"\textit{\% of raw}", "", ""]
    raw_crit = stats.get("total_critical_writes_raw", total_crit)
    for s in shorts:
        pct = totals_row[s] / raw_crit * 100 if raw_crit > 0 else 0
        pct_parts.append(f"\\textit{{{pct:.1f}\\%}}")
    lines.append("  " + " & ".join(pct_parts) + r" \\")

    lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{1mm}",
        r"\footnotesize",
        r"\textit{Columns}: "
        r"Heap = writes through pointers/struct fields/arrays (C1, C6); "
        r"Cond = feeds \texttt{if}/\texttt{while}/\texttt{for} (C2); "
        r"Ret = feeds \texttt{return} (C3); "
        r"Call = escapes via function argument (C4); "
        r"I/O = feeds file/logging output (C5); "
        r"Rsrc = \texttt{malloc}/\texttt{calloc}/\texttt{free} (C7, C8).",
        r"\end{table*}",
    ])

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"LaTeX breakdown table written to {output_path}")


def generate_csv(stats: dict, output_path: str):
    """Generate a CSV file for further analysis in R / pandas."""
    cat_short = stats.get("cat_short", {})
    sub_cols = [f"crit_{s}" for s in cat_short.values()]
    flow_cols = [f"flow_{src}_{sink}" for (src, sink) in COMPOUND_FLOWS]

    fieldnames = (["project", "dereferences", "baseline_writes",
                   "critical_writes", "dedup_critical",
                   "criticality_rate", "raw_criticality_rate"]
                  + sub_cols + flow_cols)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for p in stats["per_project"]:
            writer.writerow(p)

    print(f"CSV written to {output_path}")


def print_summary(stats: dict):
    """Print a human-readable summary."""
    dedup_total = stats.get("deduplicated_critical_total", 0)
    raw_total = stats.get("total_critical_writes_raw", 0)
    total_baseline = stats["total_baseline_writes"]

    print("\n" + "=" * 70)
    print(" LOCAL SAFETY ↔ GLOBAL SAFETY: CRITICALITY ANALYSIS")
    print("=" * 70)
    print(f"  Projects analysed:       {stats['n_projects']}")
    print(f"  Projects with data:      {stats['n_projects_with_data']}")
    print(f"  Total dereferences:      {stats['total_dereferences']:,}")
    print(f"  Total co-located writes: {total_baseline:,}")
    print(f"  Total non-critical:      {stats['total_noncritical_writes']:,}")
    print()
    print(f"  Critical writes (raw, double-counted): {raw_total:,}")
    print(f"  Critical writes (deduplicated):        {dedup_total:,}")
    print()
    print(f"  ── Aggregate criticality rate (DEDUPLICATED): "
          f"{stats['aggregate_criticality_rate']*100:.1f}% ──")
    print(f"     (raw rate before dedup: "
          f"{stats.get('raw_criticality_rate', 0)*100:.1f}%)")
    print()

    if "median_rate" in stats:
        print(f"  Per-project distribution (deduplicated rates):")
        print(f"    min    = {stats['min_rate']*100:.1f}%")
        print(f"    Q1     = {stats['q1_rate']*100:.1f}%")
        print(f"    median = {stats['median_rate']*100:.1f}%")
        print(f"    mean   = {stats['mean_rate']*100:.1f}%")
        print(f"    Q3     = {stats['q3_rate']*100:.1f}%")
        print(f"    max    = {stats['max_rate']*100:.1f}%")

    # ── Per-category breakdown (raw counts, for column table) ──
    print()
    print("  Critical write breakdown by category (raw, may overlap):")
    for cat in CRITICAL_CATEGORIES:
        key = f"critical_{cat}"
        pct = stats.get(f"{key}_pct", 0) * 100
        cnt = stats.get(key, 0)
        label = cat.replace("_", " ").title()
        print(f"    {label:25s}  {cnt:>6,}  ({pct:5.1f}%)")

    # ── Deduplication detail ──
    raw_sum = sum(stats.get(f"critical_{c}", 0) for c in CRITICAL_CATEGORIES)
    double_counted = raw_sum - dedup_total

    print()
    print("  ── Deduplication ──")
    print(f"    Sum of per-category counts (raw):   {raw_sum:>6,}")
    print(f"    Unique critical write locations:     {dedup_total:>6,}")
    print(f"    Double-counted:                     {double_counted:>6,}  "
          f"({double_counted/max(raw_sum,1)*100:.1f}%)")

    # ── Pairwise overlap matrix ──
    overlap_matrix = stats.get("overlap_matrix", {})
    if overlap_matrix:
        col_labels = {"heap": "Heap", "cond": "Cond", "ret": "Ret",
                      "call": "Call", "io": "I/O", "rsrc": "Rsrc"}
        shorts = sorted(col_labels.keys())

        print()
        print("  ── Pairwise overlap matrix (# locations in BOTH categories) ──")
        hdr = f"    {'':>6s}"
        for s in shorts:
            hdr += f"  {col_labels[s]:>5s}"
        print(hdr)

        for i, si in enumerate(shorts):
            row = f"    {col_labels[si]:>6s}"
            for j, sj in enumerate(shorts):
                if i == j:
                    row += f"  {'·':>5s}"
                else:
                    pair = tuple(sorted([si, sj]))
                    cnt = overlap_matrix.get(pair, 0)
                    row += f"  {cnt:>5d}"
            print(row)

    # ── Compound flows (from overlap matrix) ──
    print()
    total_to_cond = stats.get("total_compound_to_cond", 0)
    print(f"  ── Compound flows (from overlap matrix) ──")
    print(f"     Total locations flowing into a conditional: {total_to_cond:,}")
    if total_baseline > 0:
        print(f"     As % of all baseline writes: "
              f"{total_to_cond/total_baseline*100:.1f}%")
    print()
    for (src, sink), label in COMPOUND_FLOWS.items():
        pair = tuple(sorted([src, sink]))
        cnt = overlap_matrix.get(pair, 0)
        print(f"    {label:35s}  {cnt:>6,}")

    # ── Per-project breakdown table ──
    cat_short = stats.get("cat_short", {})
    if cat_short:
        col_labels = {"heap": "Heap", "cond": "Cond", "ret": "Ret",
                      "call": "Call", "io": "I/O", "rsrc": "Rsrc"}
        shorts = list(cat_short.values())

        print()
        print("  ── Per-project critical write breakdown ──")
        hdr = f"    {'Project':20s} {'Raw':>5s} {'Dedup':>5s} {'Rate':>6s}"
        for s in shorts:
            hdr += f"  {col_labels.get(s, s):>5s}"
        print(hdr)
        print("    " + "-" * (len(hdr) - 4))

        for p in sorted(stats["per_project"], key=lambda x: x["project"]):
            if p["baseline_writes"] == 0:
                continue
            rate_pct = p["criticality_rate"] * 100
            dedup = p.get("dedup_critical", p["critical_writes"])
            row = (f"    {p['project']:20s} {p['critical_writes']:5d} "
                   f"{dedup:5d} {rate_pct:5.1f}%")
            for s in shorts:
                row += f"  {p.get(f'crit_{s}', 0):5d}"
            print(row)

    print()
    print("  INTERPRETATION (based on deduplicated rate):")
    rate = stats["aggregate_criticality_rate"]
    if rate < 0.15:
        print("  → LOW criticality: local safety is a good proxy for global safety.")
        print("    Most writes enclosed by Skip patches are innocuous.")
    elif rate < 0.35:
        print("  → MODERATE criticality: local safety covers most cases,")
        print("    but a non-trivial fraction of writes could break behavior.")
        print("    This motivates lightweight escape analysis on top of local safety.")
    else:
        print("  → HIGH criticality: local safety is insufficient as a sole criterion.")
        print("    This motivates the need for global safety analysis.")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="NilGuard: Local→Global Safety Criticality Study"
    )
    parser.add_argument("--rules", default="rules.yaml",
                        help="Path to semgrep rules YAML")
    parser.add_argument("--dataset", default="./dataset",
                        help="Path to dataset directory")
    parser.add_argument("--output", default="./results",
                        help="Path to results directory")
    parser.add_argument("--project", default=None,
                        help="Run on a single project (for testing)")
    parser.add_argument("--reprocess", action="store_true",
                        help="Skip semgrep; regenerate tables from cached "
                             "*_raw.json files in --output")
    parser.add_argument("--exclude", default="",
                        help="Comma-separated project names to exclude "
                             "(e.g., --exclude cpython,snort3)")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    exclude_set = set(x.strip() for x in args.exclude.split(",") if x.strip())
    if exclude_set:
        print(f"Excluding projects: {', '.join(sorted(exclude_set))}\n")

    project_results = {}

    if args.reprocess:
        # ── Reprocess mode: read cached JSON, skip semgrep ──
        raw_files = sorted(Path(args.output).glob("*_raw.json"))
        if not raw_files:
            print(f"ERROR: No *_raw.json files in {args.output}")
            sys.exit(1)

        print(f"Reprocessing {len(raw_files)} cached result(s) "
              f"from {args.output}\n")

        for raw_path in raw_files:
            name = raw_path.stem.replace("_raw", "")
            if args.project and name != args.project:
                continue
            if name in exclude_set:
                print(f"[{name}] EXCLUDED")
                continue

            print(f"[{name}]")
            with open(raw_path) as f:
                data = json.load(f)
            counts = count_findings(data)
            agg = aggregate_counts(counts)
            project_results[name] = agg

            b = agg["total_baseline_writes"]
            c = agg["total_critical_writes"]
            d = agg["total_dereferences"]
            r = c / b * 100 if b > 0 else 0
            print(f"  Derefs={d:,}  Writes={b:,}  Critical={c:,}  "
                  f"Rate={r:.1f}%")
            print()

    else:
        # ── Normal mode: run semgrep ──
        dataset_path = Path(args.dataset)
        if args.project:
            projects = [dataset_path / args.project]
        else:
            projects = sorted([
                p for p in dataset_path.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ])

        if not projects:
            print(f"ERROR: No projects found in {args.dataset}")
            sys.exit(1)

        print(f"Found {len(projects)} project(s) in {args.dataset}")
        print()

        for proj_dir in projects:
            name = proj_dir.name
            if name in exclude_set:
                print(f"[{name}] EXCLUDED")
                continue
            raw_output = os.path.join(args.output, f"{name}_raw.json")

            print(f"[{name}]")
            data = run_semgrep(args.rules, str(proj_dir), raw_output)
            counts = count_findings(data)
            agg = aggregate_counts(counts)
            project_results[name] = agg

            b = agg["total_baseline_writes"]
            c = agg["total_critical_writes"]
            d = agg["total_dereferences"]
            r = c / b * 100 if b > 0 else 0
            print(f"  Derefs={d:,}  Writes={b:,}  Critical={c:,}  "
                  f"Rate={r:.1f}%")
            print()

    if not project_results:
        print("ERROR: No results to process.")
        sys.exit(1)

    # Compute aggregate statistics
    stats = compute_statistics(project_results)

    # Output
    print_summary(stats)
    generate_latex_table(stats, os.path.join(args.output, "table_criticality.tex"))
    generate_latex_breakdown_table(stats, os.path.join(args.output, "table_breakdown.tex"))
    generate_csv(stats, os.path.join(args.output, "criticality_data.csv"))

    # Save full stats as JSON
    stats_path = os.path.join(args.output, "stats.json")
    stats_for_json = {k: v for k, v in stats.items()
                      if k not in ("per_project", "cat_short")}
    # Convert tuple keys in overlap_matrix to strings for JSON
    if "overlap_matrix" in stats_for_json:
        stats_for_json["overlap_matrix"] = {
            f"{a}+{b}": cnt
            for (a, b), cnt in stats_for_json["overlap_matrix"].items()
        }
    with open(stats_path, "w") as f:
        json.dump(stats_for_json, f, indent=2)
    print(f"Full statistics written to {stats_path}")


if __name__ == "__main__":
    main()