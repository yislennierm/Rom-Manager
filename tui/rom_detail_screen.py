from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Container
from textual.screen import Screen
from textual import events

import io
import urllib.request
from textwrap import dedent
import os

from utils.paths import manufacturer_slug, console_slug
from utils.artwork import derive_artwork_url, fetch_artwork

try:
    from rich_pixels import Pixels
    from PIL import Image
except ImportError:
    Pixels = None
    Image = None

class ROMDetailScreen(Screen):
    """Display ROM metadata and optional artwork."""

    BINDINGS = [
        ("escape", "go_back", "Close"),
    ]

    def __init__(self, rom: dict, artwork_provider: str = "libretro"):
        super().__init__()
        self.rom = rom
        self.artwork_provider = artwork_provider

    def compose(self) -> ComposeResult:
        yield Header()
        children = [self._build_metadata(), self._build_artwork()]
        yield Container(*children, id="detail_container")
        yield Footer()

    def _build_metadata(self) -> Static:
        rom = self.rom
        size_label = self._format_size(rom.get("_size_bytes")) if "_size_bytes" in rom else rom.get("size") or "Unknown"
        details = dedent(
            f"""
            [b]Name:[/b] {rom.get('name', 'Unknown')}
            [b]Console:[/b] {rom.get('manufacturer', 'Unknown')} / {rom.get('console', 'Unknown')}
            [b]Size:[/b] {size_label}
            [b]MD5:[/b] {rom.get('md5') or '—'}
            [b]SHA1:[/b] {rom.get('sha1') or '—'}
            [b]CRC32:[/b] {rom.get('crc32') or '—'}
            [b]Sources:[/b] {self._source_label()}
            """
        ).strip()
        return Static(details, id="rom_metadata")

    def _source_label(self) -> str:
        sources = []
        if self.rom.get("torrent_url"):
            sources.append("torrent")
        if self.rom.get("http_url"):
            sources.append("http")
        return ", ".join(sources) or "—"

    def _build_artwork(self) -> Static:
        cache_dir = os.path.join("data", "cache", "artwork")
        artwork_path = fetch_artwork(self.rom, cache_base=cache_dir, provider_id=self.artwork_provider)
        if artwork_path and Pixels and Image:
            try:
                image = Image.open(artwork_path)
                pixels = Pixels.from_image(image)
                return Static(pixels)
            except Exception as exc:
                self._notify(f"Artwork render failed: {exc}", severity="warning")
                return Static(f"[b]Artwork path:[/b] {artwork_path}")
        elif artwork_path:
            self._notify("Artwork library unavailable; showing path instead.", severity="info")
            return Static(f"[b]Artwork path:[/b] {artwork_path}")
        self._notify("No artwork URL available for this ROM.", severity="warning")
        return Static("[b]Artwork:[/b]\nNot available")

    def _find_artwork(self) -> str | None:
        return derive_artwork_url(self.rom)

    def action_go_back(self):
        self.app.pop_screen()

    @staticmethod
    def _format_size(size_bytes):
        if size_bytes in (None, 0):
            return "Unknown"
        thresholds = [
            (1 << 40, "TB"),
            (1 << 30, "GB"),
            (1 << 20, "MB"),
            (1 << 10, "KB"),
        ]
        for factor, unit in thresholds:
            if size_bytes >= factor:
                return f"{size_bytes / factor:.1f} {unit}"
        return f"{size_bytes} B"

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")
