# Reproducing the Results
This directory contains data and scripts to reproduce the study:
1. **Tables** generates the CSVs from the per-bug metrics
2. **Experiments** runs coverage collection and SBFL ranking from the upstream Rust bug histories (requires Rust toolchain).

## Repository Layout
```
experiment/
├── README.md
├── scripts/
│   ├── sbfl_from_llvm_cov.py         SBFL: LLVM coverage JSON → ranked lines + metrics
│   ├── aggregate_metrics.py          Joins bug manifest with per-bug metric CSVs
│   ├── analyze_results.py            Produces formula/category/bug/agreement summaries
│   ├── summarize_topk.py             Extracts compact Top-k columns
│   └── reconstruct_bugs.py           Full experiment automation (clone, patch, verify, coverage, SBFL)
├── data/
│   └── bugs.csv                      28-bugs (commits, oracles, test names, result paths)
├── patches/                          Regression test patches (25 bugs; 3 bugs use existing upstream tests)
└── results/
    ├── analysis/                     Aggregate summaries
    ├── *_metrics.csv                 Per-bug SBFL metrics
    ├── *_sbfl.csv                    Per-bug ranked-line output
    ├── pilot_metrics_summary.csv     Aggregated bug-formula results
    └── topk_summary.csv              Top-k summary
```

## Requirements
For tables:
- Python 3.10 or newer

For full experiments:
- Rust toolchain with `cargo` and `cargo-llvm-cov`.
- Upstream Rust project checkouts in `../subjects/` at specific buggy and fixed commits.
- The `bugs.csv` file records the exact commit hashes and oracle locations.

## Generate Tables
```bash
cd experiment

python3 scripts/aggregate_metrics.py \
  --bugs data/bugs.csv \
  --output results/pilot_metrics_summary.csv

python3 scripts/summarize_topk.py \
  --input results/pilot_metrics_summary.csv \
  --output results/topk_summary.csv

python3 scripts/analyze_results.py \
  --input results/pilot_metrics_summary.csv \
  --out-dir results/analysis
```

Expected generated files:
```
results/pilot_metrics_summary.csv
results/topk_summary.csv
results/analysis/formula_summary.csv
results/analysis/category_summary.csv
results/analysis/bug_summary.csv
results/analysis/formula_agreement.csv
```

## Expected Key Values
- 28 bugs, 84 bug-formula rows.
- Top-1: 0/28 for all formulas.
- Top-3: 0/28 for all formulas.
- Top-5: 1/28 (strsim-jaro-single-char-panic).
- Top-10: 9/28 for all formulas.
- Median rank: 20.5 for all formulas.
- Median fault tie size: 67 for all formulas.
- 27/28 bugs have identical best ranks across Tarantula, Ochiai, and DStar².
- 28/28 bugs have identical Top-10 outcomes across formulas.

Inspect with:
```bash
cat results/analysis/formula_summary.csv
cat results/analysis/formula_agreement.csv
cat results/analysis/category_summary.csv
```

## Dataset
The 28 bugs are manually reconstructed from upstream Rust project. Each bug has:
- A known upstream repository and buggy/fixed commit pair.
- At least one study-owned regression test that fails on the buggy commit and passes on the fixed commit.
- One or more oracle source lines derived from the developer fix.
- Per-test LLVM coverage collected on the buggy version.
- SBFL rankings from Tarantula, Ochiai, and DStar².

Categories: panic-error-handling (10), parser-panic (7), macro-involved (4), generic-trait (4), standard-logic (3).

## Experiment
Each bug was reconstructed by:
1. Checking out the upstream repository at the buggy and fixed commits.
2. Applying the regression test patch from `patches/<bug_id>.patch` to both worktrees.
3. Verifying the failing test fails on buggy and passes on fixed.
4. Collecting per-test LLVM coverage on the buggy version with `cargo llvm-cov`.
5. Running `scripts/sbfl_from_llvm_cov.py` to produce the rankings and metrics.

## SBFL
The lower-level interface is `scripts/sbfl_from_llvm_cov.py`:
```bash
python3 scripts/sbfl_from_llvm_cov.py \
  --source-root <path-to-buggy-checkout> \
  --test failing:fail:<path-to-failing-coverage.json> \
  --test passing1:pass:<path-to-passing-coverage.json> \
  --test passing2:pass:<path-to-passing-coverage.json> \
  --oracle src/lib.rs:123 \
  --output <output_sbfl.csv> \
  --metrics-output <output_metrics.csv>
```
The script produces ranked source lines for Tarantula, Ochiai, and DStar², plus per-formula metrics including best rank, EXAM score, wasted effort, Top-1/3/5/10 indicators, and tie size.
