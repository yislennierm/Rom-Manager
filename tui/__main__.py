#!/usr/bin/env python3
import os

if os.environ.get("TERM") in (None, "", "dumb"):
    os.environ["TERM"] = "xterm-256color"

from .app import ROMManagerApp

if __name__ == "__main__":
    ROMManagerApp().run()
