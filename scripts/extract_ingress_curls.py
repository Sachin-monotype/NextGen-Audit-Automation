#!/usr/bin/env python3
"""
Extract audit ingress curl commands from Monotype Connect service logs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from audit_validator.desktop.config import TARGET_URL, default_log_dir  # noqa: E402
from audit_validator.desktop.log_extractor import (  # noqa: E402
    extract_ingress_events_from_logs,
    iter_log_files,
)

DEFAULT_LOG_DIR = default_log_dir()


def extract_curls(log_file: Path, *, today_only: bool = False):
    events = extract_ingress_events_from_logs(
        log_file.parent,
        today_only=today_only,
        latest_file_only=False,
    )
    by_file = [e for e in events if e.log_file == log_file.name]
    if by_file:
        return [e.raw_curl for e in by_file]

    curls = []
    with log_file.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "[CurlDebug]" not in line:
                continue
            if TARGET_URL not in line:
                continue
            curls.append(line.split("[CurlDebug]", 1)[1].strip())
    return curls


def filter_operation(curls, operation):
    if not operation:
        return curls

    needle = f'"operation":"{operation}"'
    return [c for c in curls if needle in c]


def write_curls(curls, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, curl in enumerate(curls, start=1):
        path = output_dir / f"curl_{i:03}.sh"

        with path.open("w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n\n")
            f.write(curl)
            f.write("\n")

        try:
            path.chmod(0o755)
        except Exception:
            pass

        print(f"Wrote: {path}")


def execute_curls(curls):
    for i, curl in enumerate(curls, start=1):
        print(f"\nExecuting curl #{i}...\n")

        result = subprocess.run(
            curl,
            shell=True,
        )

        if result.returncode != 0:
            print(f"curl #{i} failed ({result.returncode})")


def main():
    parser = argparse.ArgumentParser(
        description="Extract audit ingress curl commands from ConnectService logs."
    )

    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help="Location of ConnectService logs",
    )

    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only inspect the newest log file",
    )

    parser.add_argument(
        "--today-only",
        action="store_true",
        help="Only consider log files and lines from the current date",
    )

    parser.add_argument(
        "--operation",
        help="Filter by source.operation",
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="Write extracted curls to shell scripts",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to save curl scripts (default: <log-dir>\\extracted_curls)",
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute extracted curls",
    )

    args = parser.parse_args()

    if not args.log_dir.exists():
        raise SystemExit(f"Log directory does not exist:\n{args.log_dir}")

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else PROJECT_ROOT / "curls"
    )

    log_files = iter_log_files(args.log_dir, today_only=args.today_only)
    if args.latest and log_files:
        log_files = [log_files[-1]]

    if not log_files:
        raise SystemExit("No log files found.")

    curls = []

    if args.today_only:
        for ev in extract_ingress_events_from_logs(args.log_dir, today_only=True):
            curls.append(ev.raw_curl)
            print(f"{ev.log_file}:{ev.line_no}: {ev.operation} xCorrelationId={ev.x_correlation_id}")
    else:
        for log in log_files:
            extracted = extract_curls(log, today_only=False)
            if extracted:
                print(f"{log.name}: found {len(extracted)} curl(s)")
            curls.extend(extracted)

    curls = filter_operation(curls, args.operation)

    if not curls:
        print("No matching curls found.")
        return

    print(f"\nFound {len(curls)} matching curl(s).\n")

    for i, curl in enumerate(curls, start=1):
        print("=" * 100)
        print(f"CURL #{i}")
        print("=" * 100)
        print(curl)
        print()

    if args.write:
        write_curls(curls, output_dir)
        print(f"\nSaved curl scripts to:\n{output_dir}")

    if args.execute:
        execute_curls(curls)


if __name__ == "__main__":
    main()
