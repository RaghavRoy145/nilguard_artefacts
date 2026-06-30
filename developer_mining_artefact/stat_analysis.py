#!/usr/bin/env python3
"""
stat_analysis.py — Statistical correlation between local and global safety.

For each developer NPE patch, classifies it as:
  - Locally safe:   guard scope has NO writes feeding a local conditional
  - Locally unsafe: guard scope HAS writes feeding a local conditional
  - Globally safe:   guard scope has NO writes with inter-procedural conditional impact
  - Globally unsafe: guard scope HAS writes with inter-procedural conditional impact

Then tests whether local safety and global safety are correlated
using chi-square, Fisher's exact, and phi coefficient.

For the baseline study, performs the same analysis per write location:
  - Locally unsafe: location is tagged C2 (feeds local conditional)
  - Globally unsafe: location is C3/C6 with verified Joern callers

Usage:
    python3 stat_analysis.py --dev-results ./results/dev_patches
    python3 stat_analysis.py --dev-results ./results/dev_patches --baseline-results ./results --dataset ./dataset
"""

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path
from collections import defaultdict

try:
    from scipy.stats import chi2_contingency, fisher_exact
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

SKIP_PROJECTS = {"cpython"}


def phi_coefficient(table):
    """Phi coefficient for a 2x2 table [[a,b],[c,d]]."""
    a, b = table[0]
    c, d = table[1]
    num = (a * d) - (b * c)
    denom = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return num / denom if denom > 0 else 0


def proportion_ci(n, x, z=1.96):
    """Wilson score interval."""
    if n == 0:
        return 0, 0
    p = x / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denom
    return max(0, centre - margin), min(1, centre + margin)


def run_contingency_test(table, label):
    """Run chi-square, Fisher's exact, and phi on a 2x2 table."""
    a, b = table[0]
    c, d = table[1]
    n = a + b + c + d

    print(f"\n  ── {label} ──")
    print(f"  {'':25s} {'Globally Safe':>14s} {'Globally Unsafe':>16s} {'Total':>7s}")
    print(f"  {'Locally Safe':25s} {a:>14,} {b:>16,} {a+b:>7,}")
    print(f"  {'Locally Unsafe':25s} {c:>14,} {d:>16,} {c+d:>7,}")
    print(f"  {'Total':25s} {a+c:>14,} {b+d:>16,} {n:>7,}")
    print()

    # Rates
    local_safe_rate = (a + b) / n if n > 0 else 0
    global_safe_rate = (a + c) / n if n > 0 else 0
    both_safe_rate = a / n if n > 0 else 0
    print(f"  P(locally safe)  = {local_safe_rate*100:.1f}%")
    print(f"  P(globally safe) = {global_safe_rate*100:.1f}%")
    print(f"  P(both safe)     = {both_safe_rate*100:.1f}%")
    if local_safe_rate > 0:
        p_global_given_local = a / (a + b) if (a + b) > 0 else 0
        print(f"  P(globally safe | locally safe) = {p_global_given_local*100:.1f}%")
    if (c + d) > 0:
        p_global_given_not_local = c / (c + d)
        print(f"  P(globally safe | locally unsafe) = {p_global_given_not_local*100:.1f}%")
    print()

    # Statistical tests
    phi = phi_coefficient(table)
    print(f"  Phi coefficient: {phi:.4f}")
    if abs(phi) < 0.1:
        print(f"    → Negligible association")
    elif abs(phi) < 0.3:
        print(f"    → Small association")
    elif abs(phi) < 0.5:
        print(f"    → Medium association")
    else:
        print(f"    → Large association")

    if phi > 0:
        print(f"    Direction: POSITIVE — local safety predicts global safety")
    elif phi < 0:
        print(f"    Direction: NEGATIVE — local safety anti-correlates with global safety")

    if HAS_SCIPY:
        chi2, p_chi, dof, expected = chi2_contingency(table, correction=False)
        print(f"\n  Chi-square: χ² = {chi2:.4f}, df = {dof}, p = {p_chi:.2e}")

        # Check expected cell counts
        min_expected = min(expected.flatten())
        if min_expected < 5:
            print(f"    WARNING: min expected count = {min_expected:.1f} < 5, "
                  f"prefer Fisher's exact")

        oddsratio, p_fisher = fisher_exact(table)
        print(f"  Fisher's exact: OR = {oddsratio:.4f}, p = {p_fisher:.2e}")

        p_val = p_fisher  # prefer Fisher's for small cells
        if p_val < 0.001:
            print(f"\n  Result: Highly significant (p < 0.001)")
        elif p_val < 0.01:
            print(f"\n  Result: Significant (p = {p_val:.4f})")
        elif p_val < 0.05:
            print(f"\n  Result: Marginally significant (p = {p_val:.4f})")
        else:
            print(f"\n  Result: Not significant (p = {p_val:.4f})")
    else:
        print(f"\n  (pip install scipy for chi-square and Fisher's exact tests)")

    return phi


def analyze_dev_patches(dev_dir, exclude):
    """Build 2×2 table from developer patch data.

    Per PATCH (not per write):
      Locally safe   = cond_writes == 0  (no intra-proc conditional feeds)
      Globally safe  = ip_writes == 0    (no inter-proc conditional feeds)
    """
    patches = []
    for f in sorted(Path(dev_dir).glob("*_patches.json")):
        name = f.stem.replace("_patches", "")
        if name in exclude:
            continue
        with open(f) as fh:
            data = json.load(fh)
        for p in data.get("patches", []):
            p["_project"] = name
            patches.append(p)

    # Classify each patch
    a = b = c = d = 0
    for p in patches:
        local_safe = p.get("cond_writes", 0) == 0
        global_safe = p.get("ip_writes", 0) == 0

        if local_safe and global_safe:
            a += 1
        elif local_safe and not global_safe:
            b += 1
        elif not local_safe and global_safe:
            c += 1
        else:
            d += 1

    table = [[a, b], [c, d]]
    return table, patches


def analyze_baseline_per_location(results_dir, dataset_dir, exclude):
    """Build 2×2 table from baseline data at the write-location level.

    Per WRITE LOCATION:
      Locally unsafe  = tagged "cond" (C2 match: feeds local conditional)
      Globally unsafe = C3/C6 location with verified Joern callers

    Requires loading semgrep raw JSON and cross-referencing with Joern.
    """
    # Load Joern callee sets per project
    joern_dir = os.path.join(results_dir, "joern")
    joern_callees = {}  # project -> {ip1_callees, ip2_callees}

    for f in sorted(Path(joern_dir).glob("*_interproc.json")):
        name = f.stem.replace("_interproc", "")
        if name in exclude:
            continue
        with open(f) as fh:
            flows = json.load(fh)
        ip1 = {fl["callee"] for fl in flows if fl.get("type") == "IP1"}
        ip2 = {fl["callee"] for fl in flows if fl.get("type") == "IP2"}
        joern_callees[name] = {"ip1": ip1, "ip2": ip2}

    # For each project, classify each write location
    a = b = c = d = 0
    total_locations = 0

    from joern_interproc import extract_function_name_from_check

    for raw_file in sorted(Path(results_dir).glob("*_raw.json")):
        name = raw_file.stem.replace("_raw", "")
        if name in exclude:
            continue

        with open(raw_file) as fh:
            data = json.load(fh)

        callees = joern_callees.get(name, {"ip1": set(), "ip2": set()})

        # Build per-location category tags
        location_cats = defaultdict(set)  # (file, line) -> set of categories
        CRIT_CATS = {
            "C1": "heap", "C2": "cond", "C3": "ret", "C4": "call",
            "C5": "io", "C6": "outparam", "C7": "rsrc", "C8": "rsrc",
        }

        for finding in data.get("results", []):
            rule_id = finding.get("check_id", "").split(".")[-1]
            filepath = finding.get("path", "")
            line = finding.get("start", {}).get("line", 0)

            for prefix, cat in CRIT_CATS.items():
                if rule_id.startswith(prefix):
                    location_cats[(filepath, line)].add(cat)
                    if cat in ("ret", "outparam"):
                        location_cats[(filepath, line)].add(f"_{prefix}")
                    break

        # For C3/C6 locations, check if they have verified callers
        # Need function name resolution → need source files
        file_cache = {}

        for (filepath, line), cats in location_cats.items():
            if not cats:
                continue

            total_locations += 1
            is_local_unsafe = "cond" in cats
            is_global_unsafe = False

            # Check inter-procedural: is this a C3 or C6 in a function
            # with verified callers?
            has_c3 = any(c.startswith("_C3") for c in cats)
            has_c6 = any(c.startswith("_C6") for c in cats)

            if (has_c3 and callees["ip1"]) or (has_c6 and callees["ip2"]):
                # Need function name
                if filepath not in file_cache:
                    candidates = [filepath]
                    if dataset_dir:
                        for prefix in ("dataset/", "dataset\\"):
                            if filepath.startswith(prefix):
                                stripped = filepath[len(prefix):]
                                candidates.append(
                                    os.path.join(dataset_dir, stripped))
                        candidates.append(
                            os.path.join(dataset_dir, filepath))

                    content = None
                    for cand in candidates:
                        try:
                            with open(cand) as fh:
                                content = fh.read()
                            break
                        except (FileNotFoundError, IOError):
                            continue
                    file_cache[filepath] = content

                content = file_cache[filepath]
                if content:
                    func = extract_function_name_from_check(content, line)
                    if has_c3 and func in callees["ip1"]:
                        is_global_unsafe = True
                    if has_c6 and func in callees["ip2"]:
                        is_global_unsafe = True

            if not is_local_unsafe and not is_global_unsafe:
                a += 1
            elif not is_local_unsafe and is_global_unsafe:
                b += 1
            elif is_local_unsafe and not is_global_unsafe:
                c += 1
            else:
                d += 1

    table = [[a, b], [c, d]]
    return table, total_locations


def main():
    parser = argparse.ArgumentParser(
        description="Statistical test: does local safety correlate with "
                    "global safety?"
    )
    parser.add_argument("--dev-results", default="./results/dev_patches")
    parser.add_argument("--baseline-results", default="./results")
    parser.add_argument("--dataset", default="./dataset",
                        help="Dataset directory (needed for baseline analysis)")
    parser.add_argument("--exclude", default="cpython")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip baseline analysis (slow, needs source files)")
    args = parser.parse_args()

    exclude = set(x.strip() for x in args.exclude.split(",") if x.strip())
    exclude.update(SKIP_PROJECTS)

    print("=" * 70)
    print(" STATISTICAL ANALYSIS: LOCAL SAFETY ↔ GLOBAL SAFETY CORRELATION")
    print("=" * 70)
    print()
    print("  Definitions:")
    print("    Locally safe  = guard scope has NO writes feeding a")
    print("                    conditional in the same function")
    print("    Globally safe = guard scope has NO writes with verified")
    print("                    inter-procedural conditional impact")
    print()
    print("  H0: Local safety and global safety are independent")
    print("  H1: Local safety and global safety are correlated")

    # ── Developer patches (per-patch analysis) ──
    print("\n" + "=" * 70)
    print(" DEVELOPER PATCHES (per-patch, n=684)")
    print("=" * 70)

    dev_table, dev_patches = analyze_dev_patches(args.dev_results, exclude)
    n_dev = sum(sum(row) for row in dev_table)
    print(f"\n  Patches analysed: {n_dev}")

    phi_dev = run_contingency_test(
        dev_table,
        "Developer patches: local ↔ global safety"
    )

    # ── Baseline (per-write-location analysis) ──
    if not args.skip_baseline:
        print("\n" + "=" * 70)
        print(" BASELINE STUDY (per-write-location)")
        print("=" * 70)

        if os.path.isdir(args.dataset):
            bl_table, n_bl = analyze_baseline_per_location(
                args.baseline_results, args.dataset, exclude)
            print(f"\n  Write locations classified: {n_bl:,}")

            phi_bl = run_contingency_test(
                bl_table,
                "Baseline: local ↔ global safety (per location)"
            )
        else:
            print(f"\n  Dataset not found at {args.dataset}")
            print(f"  Pass --dataset ./dataset or --skip-baseline")

    # ── Summary ──
    print("\n" + "=" * 70)
    print(" SUMMARY FOR PAPER")
    print("=" * 70)
    a, b = dev_table[0]
    c, d = dev_table[1]
    n = a + b + c + d
    print(f"\n  Of {n} developer NPE patches:")
    print(f"    {a} ({a/n*100:.1f}%) are both locally and globally safe")
    print(f"    {b} ({b/n*100:.1f}%) are locally safe but globally unsafe")
    print(f"    {c} ({c/n*100:.1f}%) are locally unsafe but globally safe")
    print(f"    {d} ({d/n*100:.1f}%) are both locally and globally unsafe")
    print()
    if a + b > 0:
        p_global_given_local = a / (a + b)
        lo, hi = proportion_ci(a + b, a)
        print(f"  P(globally safe | locally safe) = {p_global_given_local*100:.1f}%"
              f"  95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    print()
    if phi_dev > 0:
        print(f"  Local and global safety are positively correlated (φ = {phi_dev:.3f}).")
        print(f"  Patches that are locally safe tend to also be globally safe.")
        print(f"  This supports using local safety as a practical proxy for")
        print(f"  global safety in NPE patch generation.")
    elif abs(phi_dev) < 0.1:
        print(f"  Local and global safety show negligible correlation (φ = {phi_dev:.3f}).")
        print(f"  They are approximately independent properties.")
    else:
        print(f"  Local and global safety are negatively correlated (φ = {phi_dev:.3f}).")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
