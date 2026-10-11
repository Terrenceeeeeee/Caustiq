#!/usr/bin/env python3
"""Install Caustiq dependencies with visible, logged progress.

This helper intentionally uses the per-user package directory outside virtual
environments so the Windows installer does not require administrator rights.
"""

from __future__ import annotations

import argparse
import locale
from pathlib import Path
import subprocess
import sys


CORE_REQUIREMENTS = (
    "numpy>=1.24",
    "pandas>=2.0",
    "scipy>=1.10",
    "matplotlib>=3.7",
    "Pillow>=10.0",
)
QT_REQUIREMENTS = ("PySide6-Essentials>=6.6",)


def stream_command(command: list[str], log_path: Path) -> int:
    """Run a command while mirroring combined output to the console and log."""
    encoding = locale.getpreferredencoding(False) or "utf-8"
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=encoding,
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        return process.wait()


def pip_install(
    requirements: tuple[str, ...],
    log_path: Path,
) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--only-binary=:all:",
        "--progress-bar=on",
        "--timeout=90",
        "--retries=2",
    ]
    if sys.prefix == sys.base_prefix:
        command.append("--user")
    command.extend(requirements)
    if stream_command(command, log_path) != 0:
        raise RuntimeError("Package installation failed. See Caustiq_startup.log for details.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("[2/5] Checking scientific packages...")
    pip_install(CORE_REQUIREMENTS, args.log)
    print()
    print("[3/5] Checking the Qt interface package...")
    print("      Downloading the Qt package from the configured Python package index.")
    pip_install(QT_REQUIREMENTS, args.log)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
