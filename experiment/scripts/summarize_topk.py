#!/usr/bin/env python3
"""Extract Top-k columns from an aggregate SBFL metrics CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = [
    "bug_id",
    "project",
    "category",
    "formula",
    "best_rank",
    "top1",
    "top5",
    "top10",
    "exam_best",
    "metrics_status",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("results/pilot_metrics_summary.csv"))
    parser.add_argument("--output", type=Path, default=Path("results/topk_summary.csv"))
    args = parser.parse_args()

    with args.input.open(newline="") as f:
        rows = list(csv.DictReader(f))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


if __name__ == "__main__":
    main()
