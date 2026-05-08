#!/usr/bin/env python3
"""End-to-end SBFL reproduction from upstream Rust repositories.

Reads data/bugs.csv and for each bug:
1. Clones the upstream repository (cached per project as a bare repo)
2. Creates buggy and fixed worktrees at the recorded commits
3. Applies regression test patch from patches/<bug_id>.patch (25 bugs)
   or uses existing upstream tests (3 bugs: chrono, rust-url-roundtrip, toml-float-overflow)
4. Verifies the failing test fails on buggy and passes on fixed
5. Collects per-test LLVM coverage on the buggy version
6. Runs sbfl_from_llvm_cov.py to produce ranked lines and metrics

Usage:
  cd reproduction
  python3 scripts/reconstruct_bugs.py                    # all bugs
  python3 scripts/reconstruct_bugs.py --bug-id chrono-round-zero
  python3 scripts/reconstruct_bugs.py --from strsim-jaro-single-char-panic
  python3 scripts/reconstruct_bugs.py --skip-clone       # skip checkout + patch
  python3 scripts/reconstruct_bugs.py --resume           # skip bugs with existing metrics
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _repro_dir() -> Path:
    return _script_dir().parent


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------

def run(cmd: list[str], cwd: Path, timeout: int = 600,
        env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, env=full_env,
    )


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def repo_slug(repo_url: str) -> str:
    path = urlparse(repo_url).path.rstrip("/")
    return path.split("/")[-1].removesuffix(".git")


def ensure_repo(repo_url: str, repos_dir: Path) -> Path:
    """Bare-clone the repo into repos_dir/<slug> if not already present."""
    slug = repo_slug(repo_url)
    dest = repos_dir / slug
    if not (dest / "HEAD").exists():
        print(f"  cloning {repo_url} -> {dest}")
        repos_dir.mkdir(parents=True, exist_ok=True)
        r = run(["git", "clone", "--bare", repo_url, str(dest)],
                cwd=repos_dir, timeout=1200)
        if r.returncode != 0:
            raise RuntimeError(f"clone failed: {r.stderr.strip()[:300]}")
    return dest


def add_worktree(bare_repo: Path, worktree: Path, commit: str):
    """Create a detached worktree from a bare repo at the given commit."""
    if worktree.exists():
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    r = run(["git", "worktree", "add", "--detach", str(worktree), commit],
            cwd=bare_repo)
    if r.returncode != 0:
        raise RuntimeError(f"worktree add failed: {r.stderr.strip()[:300]}")


# ---------------------------------------------------------------------------
# Patch analysis — infer -p <crate> from the file modified by the patch
# ---------------------------------------------------------------------------

def infer_cargo_package(patch_path: Path, oracles: list[str]) -> list[str]:
    """Return cargo -p args (e.g. ['-p', 'toml_edit']) or [] for top-level.

    Infers the workspace crate from the patch file, falling back to oracle paths.
    """
    candidate_path: str | None = None

    # Try patch first
    if patch_path.exists():
        try:
            text = patch_path.read_text()
            m = re.search(r'^[-+]{3} [ab]/(.+)$', text, re.MULTILINE)
            if m:
                candidate_path = m.group(1)
        except Exception:
            pass

    # Fall back to oracle paths
    if candidate_path is None and oracles:
        candidate_path = oracles[0].split(":")[0]

    if candidate_path is None:
        return []

    parts = Path(candidate_path).parts
    if len(parts) >= 2 and parts[0] == "crates":
        return ["-p", parts[1]]
    if len(parts) >= 2 and parts[0] not in ("src", "tests", "test"):
        return ["-p", parts[0]]
    return []


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_bug(row: dict, repro_dir: Path, subjects_dir: Path,
                artifacts_dir: Path, results_dir: Path, *,
                skip_clone: bool, skip_verify: bool) -> bool:
    bug_id = row["bug_id"]
    repo_url = row["repo_url"]
    buggy_commit = row["buggy_commit"]
    fixed_commit = row["fixed_commit"]
    failing_tests = [t.strip() for t in row["failing_tests"].split(";") if t.strip()]
    passing_tests = [t.strip() for t in row["passing_tests"].split(";") if t.strip()]
    oracles = [o.strip() for o in row["oracle"].split(";") if o.strip()]
    metrics_file = results_dir / f"{bug_id}_metrics.csv"
    sbfl_file = results_dir / f"{bug_id}_sbfl.csv"
    patch_path = repro_dir / "patches" / f"{bug_id}.patch"
    bug_artifacts = artifacts_dir / bug_id

    cargo_pkg = infer_cargo_package(patch_path, oracles)

    print(f"\n{'=' * 60}")
    print(f"[{bug_id}]  repo={repo_url}  commit={buggy_commit[:8]}..{fixed_commit[:8]}")
    print(f"  failing: {failing_tests}")
    print(f"  passing: {passing_tests}")
    print(f"  oracles: {oracles}")
    print(f"  patch:   {'yes' if patch_path.exists() else 'none (upstream tests)'}")
    if cargo_pkg:
        print(f"  crate:   {' '.join(cargo_pkg)}")

    # --- Checkout ----------------------------------------------------------
    if not skip_clone:
        repos_dir = subjects_dir / "repos"
        buggy_path = subjects_dir / f"{bug_id}-buggy"
        fixed_path = subjects_dir / f"{bug_id}-fixed"

        try:
            repo = ensure_repo(repo_url, repos_dir)
            print(f"  worktree buggy -> {buggy_path}")
            add_worktree(repo, buggy_path, buggy_commit)
            print(f"  worktree fixed -> {fixed_path}")
            add_worktree(repo, fixed_path, fixed_commit)
        except RuntimeError as e:
            print(f"  ERROR: {e}")
            return False

        if patch_path.exists():
            for label, wtdir in [("buggy", buggy_path), ("fixed", fixed_path)]:
                print(f"  applying patch to {label} ...", end=" ", flush=True)
                r = run(["git", "apply", str(patch_path)], cwd=wtdir)
                if r.returncode != 0:
                    print(f"FAILED: {r.stderr.strip()[:200]}")
                    return False
                print("OK")
    else:
        buggy_path = subjects_dir / f"{bug_id}-buggy"
        fixed_path = subjects_dir / f"{bug_id}-fixed"
        if not buggy_path.exists() or not fixed_path.exists():
            print(f"  ERROR: worktrees missing and --skip-clone is set")
            return False

    # --- Verify -------------------------------------------------------------
    if not skip_verify:
        # Failing test(s) must FAIL on buggy
        for test_name in failing_tests:
            r = run(["cargo", "test"] + cargo_pkg +
                    ["--", test_name, "--exact"],
                    cwd=buggy_path, timeout=300)
            if r.returncode == 0:
                print(f"  FAIL: '{test_name}' PASSED on buggy (expected FAIL)")
                return False
            print(f"  buggy '{test_name}' -> FAILS (expected)")

        # Failing test(s) must PASS on fixed
        for test_name in failing_tests:
            r = run(["cargo", "test"] + cargo_pkg +
                    ["--", test_name, "--exact"],
                    cwd=fixed_path, timeout=300)
            if r.returncode != 0:
                print(f"  FAIL: '{test_name}' FAILED on fixed (expected PASS)")
                return False
            print(f"  fixed '{test_name}' -> PASSES (expected)")

        # Passing tests should pass on buggy
        for test_name in passing_tests:
            r = run(["cargo", "test"] + cargo_pkg +
                    ["--", test_name, "--exact"],
                    cwd=buggy_path, timeout=300)
            if r.returncode != 0:
                print(f"  WARNING: passing test '{test_name}' FAILED on buggy")

    # --- Coverage -----------------------------------------------------------
    bug_artifacts.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    coverage_entries: list[tuple[str, str, Path]] = []

    # Failing tests: --ignore-run-fail so coverage is collected despite panic
    for test_name in failing_tests:
        slug = _slugify(test_name)
        json_path = bug_artifacts / f"failing-{slug}.json"
        print(f"  cov fail: {test_name} ...", end=" ", flush=True)
        cmd = (["cargo", "llvm-cov", "test"] + cargo_pkg +
               ["--ignore-run-fail", "--json", "--output-path", str(json_path),
                "--", test_name, "--exact"])
        r = run(cmd, cwd=buggy_path, timeout=600)
        if json_path.exists():
            print("OK")
            coverage_entries.append((test_name, "fail", json_path))
        else:
            print("MISSING")

    # Passing tests
    for test_name in passing_tests:
        slug = _slugify(test_name)
        json_path = bug_artifacts / f"passing-{slug}.json"
        print(f"  cov pass: {test_name} ...", end=" ", flush=True)
        cmd = (["cargo", "llvm-cov", "test"] + cargo_pkg +
               ["--json", "--output-path", str(json_path),
                "--", test_name, "--exact"])
        r = run(cmd, cwd=buggy_path, timeout=600)
        if json_path.exists():
            print("OK")
            coverage_entries.append((test_name, "pass", json_path))
        else:
            print("MISSING")

    if not any(outcome == "fail" for _, outcome, _ in coverage_entries):
        print("  ERROR: no failing coverage JSON produced")
        return False

    # --- SBFL ---------------------------------------------------------------
    sbfl_script = _script_dir() / "sbfl_from_llvm_cov.py"
    print(f"  SBFL ...", end=" ", flush=True)
    cmd = [
        "python3", str(sbfl_script),
        "--source-root", str(buggy_path),
    ]
    for name, outcome, json_path in coverage_entries:
        cmd.extend(["--test", f"{_slugify(name)}:{outcome}:{json_path}"])
    for oracle in oracles:
        cmd.extend(["--oracle", oracle])
    cmd.extend(["--output", str(sbfl_file)])
    cmd.extend(["--metrics-output", str(metrics_file)])

    r = run(cmd, cwd=repro_dir, timeout=120)
    if r.returncode == 0 and metrics_file.exists():
        print("OK")
        with open(metrics_file, newline="") as f:
            for mrow in csv.DictReader(f):
                if mrow.get("formula") == "ochiai":
                    print(f"  Ochiai: rank={mrow.get('best_rank','?')}  "
                          f"EXAM={mrow.get('exam_best','?')}  "
                          f"top10={mrow.get('top10','?')}  "
                          f"found={mrow.get('found','?')}")
                    break
        return True
    else:
        print("FAILED")
        if r.stderr:
            print(f"  {r.stderr.strip()[:400]}")
        return False


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name)
    return s.strip("-") or "test"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="End-to-end SBFL reproduction")
    parser.add_argument("--bugs", type=Path,
                        default=_repro_dir() / "data" / "bugs.csv")
    parser.add_argument("--subjects-dir", type=Path,
                        default=_repro_dir() / "subjects")
    parser.add_argument("--artifacts-dir", type=Path,
                        default=_repro_dir() / "artifacts")
    parser.add_argument("--results-dir", type=Path,
                        default=_repro_dir() / "results")
    parser.add_argument("--bug-id", action="append", default=[])
    parser.add_argument("--from", dest="from_bug",
                        help="Start from this bug ID (inclusive)")
    parser.add_argument("--skip-clone", action="store_true",
                        help="Skip clone, worktree creation, and patching")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip test verification")
    parser.add_argument("--resume", action="store_true",
                        help="Skip bugs whose metrics CSV already exists")
    args = parser.parse_args()

    if not args.bugs.exists():
        print(f"ERROR: bugs CSV not found: {args.bugs}")
        sys.exit(1)

    all_bugs = list(csv.DictReader(open(args.bugs, newline="")))

    if args.bug_id:
        bug_ids = set(args.bug_id)
        bugs = [b for b in all_bugs if b["bug_id"] in bug_ids]
        if not bugs:
            print("ERROR: no bugs matched --bug-id filter")
            sys.exit(1)
    elif args.from_bug:
        bugs = []
        found = False
        for b in all_bugs:
            if b["bug_id"] == args.from_bug:
                found = True
            if found:
                bugs.append(b)
        if not bugs:
            print(f"ERROR: --from bug '{args.from_bug}' not found")
            sys.exit(1)
    else:
        bugs = all_bugs

    if args.resume:
        before = len(bugs)
        bugs = [b for b in bugs
                if not (args.results_dir / f"{b['bug_id']}_metrics.csv").exists()]
        skipped = before - len(bugs)
        if skipped:
            print(f"Resume: skipping {skipped} bug(s) with existing metrics")

    print(f"Processing {len(bugs)} bug(s)")
    print(f"  subjects:  {args.subjects_dir}")
    print(f"  artifacts: {args.artifacts_dir}")
    print(f"  results:   {args.results_dir}")

    ok = 0
    failed = 0
    for bug in bugs:
        success = process_bug(
            bug, _repro_dir(),
            subjects_dir=args.subjects_dir,
            artifacts_dir=args.artifacts_dir,
            results_dir=args.results_dir,
            skip_clone=args.skip_clone,
            skip_verify=args.skip_verify,
        )
        if success:
            ok += 1
        else:
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Done: {ok} succeeded, {failed} failed (out of {len(bugs)})")


if __name__ == "__main__":
    main()
