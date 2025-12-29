#!/usr/bin/env python3
import os
import sys

from .app import ROMManagerApp


def _extract_start_screen(argv: list[str]) -> tuple[str | None, list[str]]:
    """Parse --screen/--start-screen from argv and return (value, cleaned_args)."""
    start_screen = None
    cleaned = []
    skip_next = False

    for idx, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue

        if arg.startswith("--screen="):
            start_screen = arg.split("=", 1)[1]
            continue
        if arg.startswith("--start-screen="):
            start_screen = arg.split("=", 1)[1]
            continue
        if arg in ("--screen", "--start-screen"):
            if idx + 1 < len(argv):
                start_screen = argv[idx + 1]
                skip_next = True
            continue

        cleaned.append(arg)

    return start_screen, cleaned


if __name__ == "__main__":
    start, cleaned_args = _extract_start_screen(sys.argv[1:])
    if start:
        os.environ["TUI_START_SCREEN"] = start
        sys.argv = [sys.argv[0], *cleaned_args]

    ROMManagerApp().run()
