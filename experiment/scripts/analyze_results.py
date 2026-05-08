#!/usr/bin/env python3
"""Summarize SBFL pilot results for the paper tables."""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


def truth(value: str) -> bool:
    return value.strip().lower() == "true"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize_formula(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_formula: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_formula[row["formula"]].append(row)

    output = []
    for formula, items in sorted(by_formula.items()):
        exams = [float(row["exam_best"]) for row in items]
        ranks = [int(row["best_rank"]) for row in items]
        ties = [int(row["tie_size"]) for row in items]
        output.append(
            {
                "formula": formula,
                "bugs": len(items),
                "found": sum(truth(row["found"]) for row in items),
                "top1": sum(truth(row["top1"]) for row in items),
                "top3": sum(truth(row["top3"]) for row in items),
                "top5": sum(truth(row["top5"]) for row in items),
                "top10": sum(truth(row["top10"]) for row in items),
                "mean_exam": f"{mean(exams):.6f}",
                "median_exam": f"{median(exams):.6f}",
                "mean_rank": f"{mean(ranks):.2f}",
                "median_rank": f"{median(ranks):.2f}",
                "mean_tie_size": f"{mean(ties):.2f}",
                "median_tie_size": f"{median(ties):.2f}",
            }
        )
    return output


def summarize_category(rows: list[dict[str, str]], formula: str) -> list[dict[str, object]]:
    filtered = [row for row in rows if row["formula"] == formula]
    by_category: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in filtered:
        by_category[row["category"]].append(row)

    output = []
    for category, items in sorted(by_category.items()):
        exams = [float(row["exam_best"]) for row in items]
        ranks = [int(row["best_rank"]) for row in items]
        ties = [int(row["tie_size"]) for row in items]
        output.append(
            {
                "category": category,
                "bugs": len(items),
                "top1": sum(truth(row["top1"]) for row in items),
                "top3": sum(truth(row["top3"]) for row in items),
                "top5": sum(truth(row["top5"]) for row in items),
                "top10": sum(truth(row["top10"]) for row in items),
                "mean_exam": f"{mean(exams):.6f}",
                "median_exam": f"{median(exams):.6f}",
                "mean_rank": f"{mean(ranks):.2f}",
                "median_rank": f"{median(ranks):.2f}",
                "mean_tie_size": f"{mean(ties):.2f}",
                "median_tie_size": f"{median(ties):.2f}",
            }
        )
    return output


def summarize_bugs(rows: list[dict[str, str]], formula: str) -> list[dict[str, object]]:
    filtered = [row for row in rows if row["formula"] == formula]
    output = []
    for row in sorted(filtered, key=lambda item: (item["category"], item["bug_id"])):
        output.append(
            {
                "bug_id": row["bug_id"],
                "project": row["project"],
                "category": row["category"],
                "best_rank": row["best_rank"],
                "tie_size": row["tie_size"],
                "exam_best": f"{float(row['exam_best']):.6f}",
                "top1": row["top1"].lower(),
                "top3": row["top3"].lower(),
                "top5": row["top5"].lower(),
                "top10": row["top10"].lower(),
                "oracle": row["oracle"],
            }
        )
    return output


def formula_disagreements(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_bug: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_bug[row["bug_id"]].append(row)

    output = []
    for bug_id, items in sorted(by_bug.items()):
        ranks = {row["formula"]: int(row["best_rank"]) for row in items}
        top10 = {row["formula"]: truth(row["top10"]) for row in items}
        exams = {row["formula"]: float(row["exam_best"]) for row in items}
        output.append(
            {
                "bug_id": bug_id,
                "rank_values": ";".join(f"{key}:{value}" for key, value in sorted(ranks.items())),
                "same_rank": len(set(ranks.values())) == 1,
                "same_top10": len(set(top10.values())) == 1,
                "exam_range": f"{(max(exams.values()) - min(exams.values())):.6f}",
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("results/pilot_metrics_summary.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("results/analysis"))
    parser.add_argument("--category-formula", default="ochiai")
    args = parser.parse_args()

    rows = [row for row in read_rows(args.input) if row.get("metrics_status") == "ok"]

    write_csv(
        args.out_dir / "formula_summary.csv",
        summarize_formula(rows),
        [
            "formula",
            "bugs",
            "found",
            "top1",
            "top3",
            "top5",
            "top10",
            "mean_exam",
            "median_exam",
            "mean_rank",
            "median_rank",
            "mean_tie_size",
            "median_tie_size",
        ],
    )
    write_csv(
        args.out_dir / "category_summary.csv",
        summarize_category(rows, args.category_formula),
        [
            "category",
            "bugs",
            "top1",
            "top3",
            "top5",
            "top10",
            "mean_exam",
            "median_exam",
            "mean_rank",
            "median_rank",
            "mean_tie_size",
            "median_tie_size",
        ],
    )
    write_csv(
        args.out_dir / "bug_summary.csv",
        summarize_bugs(rows, args.category_formula),
        [
            "bug_id",
            "project",
            "category",
            "best_rank",
            "tie_size",
            "exam_best",
            "top1",
            "top3",
            "top5",
            "top10",
            "oracle",
        ],
    )
    write_csv(
        args.out_dir / "formula_agreement.csv",
        formula_disagreements(rows),
        ["bug_id", "rank_values", "same_rank", "same_top10", "exam_range"],
    )


if __name__ == "__main__":
    main()
