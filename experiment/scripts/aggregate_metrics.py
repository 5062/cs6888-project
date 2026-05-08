#!/usr/bin/env python3
"""Aggregate per-bug SBFL metrics into a study-level summary CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


BUG_FIELDS = [
    "bug_id",
    "project",
    "category",
    "repo_url",
    "buggy_commit",
    "fixed_commit",
    "failing_tests",
    "passing_tests",
    "oracle",
    "notes",
]

METRIC_FIELDS = [
    "formula",
    "found",
    "best_rank",
    "worst_tie_rank",
    "average_tie_rank",
    "tie_size",
    "exam_best",
    "exam_worst_tie",
    "wasted_effort_best",
    "top1",
    "top3",
    "top5",
    "top10",
    "ranked_lines",
    "best_oracle_file",
    "best_oracle_line",
    "best_oracle_text",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def normalize_float(value: str, digits: int = 6) -> str:
    if value == "":
        return value
    try:
        return f"{float(value):.{digits}f}"
    except ValueError:
        return value


def aggregate(bugs_path: Path, root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for bug in read_csv(bugs_path):
        metrics_path = root / bug["metrics_file"]
        if not metrics_path.exists():
            rows.append(
                {
                    **{field: bug.get(field, "") for field in BUG_FIELDS},
                    **{field: "" for field in METRIC_FIELDS},
                    "metrics_status": "missing",
                }
            )
            continue

        for metric in read_csv(metrics_path):
            normalized_metric = dict(metric)
            normalized_metric["exam_best"] = normalize_float(normalized_metric.get("exam_best", ""))
            normalized_metric["exam_worst_tie"] = normalize_float(normalized_metric.get("exam_worst_tie", ""))
            normalized_metric["average_tie_rank"] = normalize_float(normalized_metric.get("average_tie_rank", ""))
            rows.append(
                {
                    **{field: bug.get(field, "") for field in BUG_FIELDS},
                    **{field: normalized_metric.get(field, "") for field in METRIC_FIELDS},
                    "metrics_status": "ok",
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bugs", type=Path, default=Path("data/bugs.csv"))
    parser.add_argument("--output", type=Path, default=Path("results/pilot_metrics_summary.csv"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    rows = aggregate(args.bugs, args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = BUG_FIELDS + METRIC_FIELDS + ["metrics_status"]
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
