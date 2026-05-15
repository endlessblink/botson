#!/usr/bin/env python3
"""T-160: one-command runner for the Phase-27 control-surface trust lane.

Bundles the trust-lane checks behind a single entry point so a dashboard
change cannot bypass the suite by running only narrow unit tests.

What it runs (in order)
-----------------------
1. pytest on the trust-lane test files (T-153..T-159):
     tests/test_scheduler_lifecycle_e2e.py
     tests/test_planner_review_drafts_e2e.py
     tests/test_send_now_parity.py
     tests/test_diagnostics_visibility.py
     tests/test_e2e_den_smoke.py
2. pytest on the supporting planner/calendar contracts:
     tests/test_planner_coercion_and_chips.py
     tests/test_planner_visual.py
     tests/test_calendar_scheduled_games.py
3. The hardcoded-content guardian:
     tests/test_no_hardcoded_content.py
4. scripts/e2e_den_smoke.py --dry-run  (never sends — pure rendering check)

Usage
-----
    PYTHONPATH=. uv run python scripts/run_control_surface_e2e.py

Flags
-----
    --skip-smoke   Skip the Den dry-run section (rarely useful; kept for
                   environments where scripts/ imports can't resolve).
    --verbose      Stream pytest output live instead of capturing.
    --json         Emit a JSON summary on stdout for CI parsing.

Exit code
---------
0 iff every section passes. 1 on any failure. Failures point to the
relevant section name; the underlying pytest output is replayed verbatim.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Section:
    """One named runnable section of the gate."""
    name: str
    command: list[str]
    description: str = ""


@dataclass
class SectionResult:
    name: str
    exit_code: int
    duration_s: float
    output: str = ""
    skipped: bool = False
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.skipped or self.exit_code == 0


TRUST_LANE_TESTS = [
    "tests/test_scheduler_lifecycle_e2e.py",     # T-156
    "tests/test_planner_review_drafts_e2e.py",   # T-155
    "tests/test_send_now_parity.py",             # T-157
    "tests/test_diagnostics_visibility.py",      # T-158
    "tests/test_e2e_den_smoke.py",               # T-159 (dry-run unit tests)
]

SUPPORTING_TESTS = [
    "tests/test_planner_coercion_and_chips.py",  # T-153 helpers
    "tests/test_planner_visual.py",              # T-153 visual contract
    "tests/test_calendar_scheduled_games.py",    # historic dispatch coverage
]


def _build_sections(skip_smoke: bool) -> list[Section]:
    pytest_base = ["uv", "run", "pytest", "-q", "--no-header"]
    sections = [
        Section(
            name="trust-lane",
            command=pytest_base + TRUST_LANE_TESTS,
            description="T-155..T-159 trust-lane contracts",
        ),
        Section(
            name="supporting",
            command=pytest_base + SUPPORTING_TESTS,
            description="Adjacent planner / calendar contracts",
        ),
        Section(
            name="hardcoded-guardian",
            command=pytest_base + ["tests/test_no_hardcoded_content.py"],
            description="No-hardcoded-content guardian",
        ),
    ]
    if not skip_smoke:
        sections.append(Section(
            name="den-smoke-dry-run",
            command=["uv", "run", "python", "scripts/e2e_den_smoke.py"],
            description="Sherlocks Den smoke harness — dry-run only",
        ))
    return sections


def _run_section(section: Section, *, verbose: bool) -> SectionResult:
    start = time.monotonic()
    env = {"PYTHONPATH": str(REPO_ROOT)}
    # Inherit parent env so uv / PATH still work.
    import os
    merged = {**os.environ, **env}
    if verbose:
        print(f"\n══ {section.name} ══ {section.description}")
        print(f"$ {' '.join(section.command)}")
        proc = subprocess.run(section.command, cwd=REPO_ROOT, env=merged)
        return SectionResult(
            name=section.name,
            exit_code=proc.returncode,
            duration_s=time.monotonic() - start,
            output="(streamed live)",
        )
    proc = subprocess.run(
        section.command, cwd=REPO_ROOT, env=merged,
        capture_output=True, text=True,
    )
    return SectionResult(
        name=section.name,
        exit_code=proc.returncode,
        duration_s=time.monotonic() - start,
        output=(proc.stdout or "") + (proc.stderr or ""),
    )


def _print_summary(results: list[SectionResult]) -> None:
    print()
    print("=" * 64)
    print("Control-surface E2E trust lane — summary")
    print("=" * 64)
    name_w = max(len(r.name) for r in results)
    for r in results:
        if r.skipped:
            tag = "SKIP"
            extra = f"  ({r.skip_reason})" if r.skip_reason else ""
        else:
            tag = "PASS" if r.ok else "FAIL"
            extra = ""
        print(f"  [{tag}] {r.name.ljust(name_w)}  {r.duration_s:5.2f}s{extra}")
    print()
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"{len(failed)} section(s) failed:")
        for r in failed:
            print(f"\n── {r.name} output ──")
            print(r.output.rstrip())
    else:
        print(f"All {len(results)} sections passed.")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0] if __doc__ else "control-surface e2e runner",
    )
    parser.add_argument("--skip-smoke", action="store_true",
                        help="Skip the Den dry-run section")
    parser.add_argument("--verbose", action="store_true",
                        help="Stream pytest output live")
    parser.add_argument("--json", dest="emit_json", action="store_true",
                        help="Emit a JSON summary on stdout for CI parsing")
    args = parser.parse_args(argv)

    sections = _build_sections(skip_smoke=args.skip_smoke)
    results: list[SectionResult] = []
    for section in sections:
        result = _run_section(section, verbose=args.verbose)
        results.append(result)
        if not args.verbose and not args.emit_json:
            tag = "PASS" if result.ok else "FAIL"
            print(f"  [{tag}] {section.name}  ({result.duration_s:.2f}s)")
            sys.stdout.flush()

    overall = 0 if all(r.ok for r in results) else 1
    if args.emit_json:
        print(json.dumps({
            "ok": overall == 0,
            "sections": [
                {"name": r.name, "ok": r.ok, "exit_code": r.exit_code,
                 "duration_s": round(r.duration_s, 3),
                 "skipped": r.skipped, "skip_reason": r.skip_reason}
                for r in results
            ],
        }, indent=2))
    else:
        _print_summary(results)
    return overall


if __name__ == "__main__":
    sys.exit(main())
