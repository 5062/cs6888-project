#!/usr/bin/env python3
"""Compute line-level SBFL scores from cargo-llvm-cov JSON exports.

This pilot parser intentionally uses a simple line abstraction:
any executable coverage segment on a source line makes the line executable,
and any positive segment count marks that line as executed by the test.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestSpec:
    name: str
    outcome: str
    path: Path


@dataclass(frozen=True)
class OracleLine:
    file: str
    line: int


def parse_test_spec(raw: str) -> TestSpec:
    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("test spec must be NAME:pass|fail:PATH")
    name, outcome, path = parts
    if outcome not in {"pass", "fail"}:
        raise argparse.ArgumentTypeError("outcome must be 'pass' or 'fail'")
    return TestSpec(name=name, outcome=outcome, path=Path(path))


def parse_oracle(raw: str) -> OracleLine:
    if ":" not in raw:
        raise argparse.ArgumentTypeError("oracle must be FILE:LINE")
    filename, line = raw.rsplit(":", 1)
    if not filename:
        raise argparse.ArgumentTypeError("oracle file must not be empty")
    try:
        line_number = int(line)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("oracle line must be an integer") from exc
    if line_number <= 0:
        raise argparse.ArgumentTypeError("oracle line must be positive")
    return OracleLine(filename.replace("\\", "/"), line_number)


def relative_to_root(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        resolved_root = root.resolve()
        parts = path.resolve().parts
        for index in range(len(parts)):
            suffix = Path(*parts[index:])
            if suffix.is_absolute():
                continue
            if (resolved_root / suffix).exists():
                return suffix.as_posix()
        return None


def load_lines(spec: TestSpec, source_root: Path) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    data = json.loads(spec.path.read_text())
    executable: set[tuple[str, int]] = set()
    executed: set[tuple[str, int]] = set()

    for datum in data.get("data", []):
        for file_entry in datum.get("files", []):
            filename = relative_to_root(Path(file_entry["filename"]), source_root)
            if filename is None or not filename.endswith(".rs"):
                continue
            for segment in file_entry.get("segments", []):
                line, _col, count, has_count, _is_region_entry, _is_gap = segment
                if not has_count:
                    continue
                key = (filename, int(line))
                executable.add(key)
                if int(count) > 0:
                    executed.add(key)

    if executable:
        return executable, executed

    # Some cargo-llvm-cov modes, notably --dep-coverage for a path dependency
    # driven by a wrapper binary, can emit function regions but an empty files
    # list. Use the function-region line starts as a conservative fallback.
    for datum in data.get("data", []):
        for function in datum.get("functions", []):
            filenames = function.get("filenames", [])
            if not filenames:
                continue
            filename = relative_to_root(Path(filenames[0]), source_root)
            if filename is None or not filename.endswith(".rs"):
                continue
            for region in function.get("regions", []):
                if len(region) < 5:
                    continue
                line = int(region[0])
                count = int(region[4])
                key = (filename, line)
                executable.add(key)
                if count > 0:
                    executed.add(key)
    return executable, executed


def tarantula(ef: int, ep: int, nf: int, np: int) -> float:
    fail_rate = ef / (ef + nf) if ef + nf else 0.0
    pass_rate = ep / (ep + np) if ep + np else 0.0
    denom = fail_rate + pass_rate
    return fail_rate / denom if denom else 0.0


def ochiai(ef: int, ep: int, nf: int, _np: int) -> float:
    denom = math.sqrt((ef + nf) * (ef + ep))
    return ef / denom if denom else 0.0


def dstar2(ef: int, ep: int, nf: int, _np: int) -> float:
    denom = ep + nf
    if denom == 0:
        return math.inf if ef else 0.0
    return (ef * ef) / denom


def line_text(source_root: Path, rel_file: str, line: int) -> str:
    path = source_root / rel_file
    try:
        lines = path.read_text().splitlines()
    except UnicodeDecodeError:
        return ""
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def test_module_lines(source_root: Path, rel_file: str) -> set[int]:
    path = source_root / rel_file
    try:
        lines = path.read_text().splitlines()
    except UnicodeDecodeError:
        return set()

    excluded: set[int] = set()
    in_test_mod = False
    depth = 0

    for index, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not in_test_mod:
            recent = "\n".join(lines[max(0, index - 4) : index])
            if re.search(r"\bmod\s+\w+\b", stripped) and "#[cfg(test)]" in recent:
                in_test_mod = True
                depth = 0
        if in_test_mod:
            excluded.add(index)
            depth += text.count("{") - text.count("}")
            if depth <= 0 and "{" in text:
                in_test_mod = False
    return excluded


def score_key(row: dict[str, object], formula: str) -> tuple[float, float, float]:
    if formula == "ochiai":
        return (float(row["ochiai"]), float(row["dstar2"]), float(row["tarantula"]))
    if formula == "dstar2":
        return (float(row["dstar2"]), float(row["ochiai"]), float(row["tarantula"]))
    if formula == "tarantula":
        return (float(row["tarantula"]), float(row["ochiai"]), float(row["dstar2"]))
    raise ValueError(f"unsupported formula: {formula}")


def sort_rows(rows: list[dict[str, object]], formula: str) -> None:
    rows.sort(
        key=lambda row: (
            -score_key(row, formula)[0],
            -score_key(row, formula)[1],
            -score_key(row, formula)[2],
            str(row["file"]),
            int(row["line"]),
        )
    )


def write_ranking(rows: list[dict[str, object]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["rank", "file", "line", "ef", "ep", "nf", "np", "tarantula", "ochiai", "dstar2", "text"],
            lineterminator="\n",
        )
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow({"rank": index, **row})


def compute_metrics(rows: list[dict[str, object]], oracles: list[OracleLine], formula: str) -> dict[str, object]:
    if not oracles:
        raise ValueError("at least one oracle line is required")
    ranked_rows = list(rows)
    sort_rows(ranked_rows, formula)
    oracle_keys = {(oracle.file, oracle.line) for oracle in oracles}
    indexed_rows = [(index, row) for index, row in enumerate(ranked_rows, start=1)]
    matching = [(index, row) for index, row in indexed_rows if (row["file"], row["line"]) in oracle_keys]
    if not matching:
        return {
            "formula": formula,
            "oracle": ";".join(f"{oracle.file}:{oracle.line}" for oracle in oracles),
            "found": "false",
            "best_rank": "",
            "worst_tie_rank": "",
            "average_tie_rank": "",
            "tie_size": "",
            "exam_best": "",
            "exam_worst_tie": "",
            "wasted_effort_best": "",
            "top1": "false",
            "top3": "false",
            "top5": "false",
            "top10": "false",
            "ranked_lines": len(rows),
            "best_oracle_file": "",
            "best_oracle_line": "",
            "best_oracle_text": "",
        }

    best_rank, best_row = min(matching, key=lambda item: item[0])
    best_score = score_key(best_row, formula)[0]
    tied_ranks = [
        index
        for index, row in indexed_rows
        if score_key(row, formula)[0] == best_score
    ]
    worst_tie_rank = max(tied_ranks)
    average_tie_rank = sum(tied_ranks) / len(tied_ranks)

    return {
        "formula": formula,
        "oracle": ";".join(f"{oracle.file}:{oracle.line}" for oracle in oracles),
        "found": "true",
        "best_rank": best_rank,
        "worst_tie_rank": worst_tie_rank,
        "average_tie_rank": average_tie_rank,
        "tie_size": len(tied_ranks),
        "exam_best": best_rank / len(rows) if rows else "",
        "exam_worst_tie": worst_tie_rank / len(rows) if rows else "",
        "wasted_effort_best": best_rank - 1,
        "top1": str(best_rank <= 1).lower(),
        "top3": str(best_rank <= 3).lower(),
        "top5": str(best_rank <= 5).lower(),
        "top10": str(best_rank <= 10).lower(),
        "ranked_lines": len(rows),
        "best_oracle_file": best_row["file"],
        "best_oracle_line": best_row["line"],
        "best_oracle_text": best_row["text"],
    }


def write_metrics(metrics: list[dict[str, object]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "formula",
        "oracle",
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
    with output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(metrics)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--test", action="append", required=True, type=parse_test_spec)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--formula", choices=["ochiai", "dstar2", "tarantula"], default="ochiai")
    parser.add_argument("--oracle", action="append", type=parse_oracle)
    parser.add_argument("--metrics-output", type=Path)
    args = parser.parse_args()

    specs: list[TestSpec] = args.test
    failing = [spec for spec in specs if spec.outcome == "fail"]
    passing = [spec for spec in specs if spec.outcome == "pass"]
    if not failing:
        raise SystemExit("at least one failing test is required")
    if not passing:
        raise SystemExit("at least one passing test is required")

    executable_by_test: dict[str, set[tuple[str, int]]] = {}
    executed_by_test: dict[str, set[tuple[str, int]]] = {}
    executable_lines: set[tuple[str, int]] = set()
    for spec in specs:
        executable, executed = load_lines(spec, args.source_root)
        executable_by_test[spec.name] = executable
        executed_by_test[spec.name] = executed
        executable_lines.update(executable)

    excluded_lines: dict[str, set[int]] = {}
    for filename, _line in executable_lines:
        if filename not in excluded_lines:
            excluded_lines[filename] = test_module_lines(args.source_root, filename)

    rows: list[dict[str, object]] = []
    for filename, line in sorted(executable_lines):
        if line in excluded_lines.get(filename, set()):
            continue
        ef = sum((filename, line) in executed_by_test[spec.name] for spec in failing)
        ep = sum((filename, line) in executed_by_test[spec.name] for spec in passing)
        nf = len(failing) - ef
        np = len(passing) - ep
        rows.append(
            {
                "file": filename,
                "line": line,
                "ef": ef,
                "ep": ep,
                "nf": nf,
                "np": np,
                "tarantula": tarantula(ef, ep, nf, np),
                "ochiai": ochiai(ef, ep, nf, np),
                "dstar2": dstar2(ef, ep, nf, np),
                "text": line_text(args.source_root, filename, line),
            }
        )

    sort_rows(rows, args.formula)
    write_ranking(rows, args.output)

    if args.metrics_output:
        if not args.oracle:
            raise SystemExit("--metrics-output requires at least one --oracle FILE:LINE")
        metrics = [compute_metrics(rows, args.oracle, formula) for formula in ("ochiai", "dstar2", "tarantula")]
        write_metrics(metrics, args.metrics_output)


if __name__ == "__main__":
    main()
