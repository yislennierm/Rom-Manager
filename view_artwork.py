#!/usr/bin/env python3
"""
Quick Textual viewer to preview remote artwork images inside the terminal.

Usage:
    python view_artwork.py https://raw.githubusercontent.com/.../Named_Boxarts/Game.png
"""

from __future__ import annotations

import argparse
import io
import sys
from typing import Tuple

import requests
from PIL import Image
from rich_pixels import Pixels
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static


def fetch_pixels(url: str) -> Tuple[Pixels, Tuple[int, int]]:
    """Download an image from ``url`` and convert it to a Pixellated renderable."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    buffer = io.BytesIO(response.content)
    image = Image.open(buffer)
    return Pixels.from_image(image), image.size


class ArtworkViewer(App):
    """Minimal Textual app that renders a Pixels instance."""

    CSS = """
    Screen {
        align: center middle;
        background: #101010;
    }

    #info {
        padding: 1 2;
    }

    #artwork {
        padding: 1;
    }
    """

    def __init__(self, pixels: Pixels, size: Tuple[int, int], title_text: str):
        super().__init__()
        self._pixels = pixels
        self._size = size
        self._title_text = title_text

    def compose(self) -> ComposeResult:
        yield Header()
        width, height = self._size
        info = Static(
            f"[b]Source:[/b] {self._title_text}\n[b]Size:[/b] {width}×{height}px",
            id="info",
        )
        artwork = Static(self._pixels, id="artwork")
        yield Container(info, artwork)
        yield Footer()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="View libretro artwork in the terminal.")
    parser.add_argument("url", help="Direct URL to the PNG artwork (e.g., libretro Named_Boxarts asset).")
    args = parser.parse_args(argv)

    try:
        pixels, size = fetch_pixels(args.url)
    except Exception as exc:  # pragma: no cover - CLI feedback
        print(f"❌ Failed to fetch artwork: {exc}")
        return 1

    viewer = ArtworkViewer(pixels, size, args.url)
    viewer.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
