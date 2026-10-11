#!/usr/bin/env python3
"""Start the GUI with persistent diagnostics instead of a silent console exit."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import platform
import runpy
import sys
import traceback


ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "Caustiq_startup.log"


def _write_failure(exc: BaseException) -> str:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    report = (
        "\nGUI STARTUP FAILURE\n"
        f"Time: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Platform: {platform.platform()}\n"
        f"Python: {sys.version}\n"
        f"Executable: {sys.executable}\n"
        f"Working folder: {ROOT}\n\n"
        f"{detail}\n"
    )
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(report)
    return detail


def _show_error(detail: str) -> None:
    short_detail = detail[-1800:]
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(
            None,
            "Caustiq startup failed",
            "The GUI could not start.\n\n"
            f"{short_detail}\n\n"
            f"Full log: {LOG_FILE}",
        )
    except Exception:
        pass


def main() -> int:
    os.chdir(ROOT)
    try:
        runpy.run_path(str(ROOT / "unified_gui.py"), run_name="__main__")
    except KeyboardInterrupt:
        return 130
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            return 0
        detail = _write_failure(exc)
        print(detail, file=sys.stderr)
        print(f"Full startup log: {LOG_FILE}", file=sys.stderr)
        _show_error(detail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
