import os
import sys

from textual.app import App

from core.download_manager import DownloadManager
from utils.paths import list_cached_consoles, manufacturer_slug, console_slug
from .menu_screen import MenuScreen
from .home_screen import HomeScreen
from .bagels_preview_screen import BagelsPreviewScreen


class ROMManagerApp(App):
    TITLE = "ROMs Manager"
    SUB_TITLE = "LaRaspa"
    CSS_PATH = "styles/base.css"

    def __init__(self, **kwargs):
        # Ensure a default theme is set before Textual initializes stylesheets.
        self._theme_name = os.environ.get("TUI_THEME", "dark")
        super().__init__(**kwargs)

    # Simple theme palette inspired by Bagels.
    THEMES = {
        "dark": {
            "background": "#0f172a",
            "panel": "#111827",
            "surface": "#1f2937",
            "surface-lighten-1": "#273449",
            "surface-lighten-2": "#30405a",
            "surface-darken-1": "#0d1424",
            "panel-darken-2": "#0b101c",
            "text-primary": "#e5e7eb",
            "text-secondary": "#9ca3af",
            "accent": "#3b82f6",
            "accent-lighten-1": "#5b9cfa",
        },
        "nord": {
            "background": "#2E3440",
            "surface": "#3B4252",
            "panel": "#434C5E",
            "surface-lighten-1": "#4C566A",
            "surface-lighten-2": "#5E6474",
            "surface-darken-1": "#2b2f3b",
            "panel-darken-2": "#262a34",
            "text-primary": "#D8DEE9",
            "text-secondary": "#AEB6C2",
            "accent": "#88C0D0",
            "accent-lighten-1": "#9fd0dd",
        },
        "dracula": {
            "background": "#282A36",
            "surface": "#2B2E3B",
            "panel": "#313442",
            "surface-lighten-1": "#3B3F50",
            "surface-lighten-2": "#44495F",
            "surface-darken-1": "#232634",
            "panel-darken-2": "#292c3a",
            "text-primary": "#F8F8F2",
            "text-secondary": "#BCC0C7",
            "accent": "#BD93F9",
            "accent-lighten-1": "#c9a5fb",
        },
        "hacker": {
            "background": "#0D0D0D",
            "surface": "#1A1A1A",
            "panel": "#2A2A2A",
            "surface-lighten-1": "#343434",
            "surface-lighten-2": "#3E3E3E",
            "surface-darken-1": "#111111",
            "panel-darken-2": "#1f1f1f",
            "text-primary": "#E5FFE5",
            "text-secondary": "#8FE58F",
            "accent": "#00FF00",
            "accent-lighten-1": "#33ff33",
        },
    }

    BINDINGS = [
        ("ctrl+t", "cycle_theme", "Theme"),
    ]

    def get_css_variables(self) -> dict[str, str]:
        """Provide a simple palette for consistent theming."""
        theme_name = getattr(self, "_theme_name", None) or os.environ.get("TUI_THEME", "dark")
        # Guard against non-hashable or unexpected values.
        if not isinstance(theme_name, str):
            theme_name = "dark"
        palette = self.THEMES.get(theme_name, self.THEMES["dark"])
        return {**super().get_css_variables(), **palette}

    @staticmethod
    def _get_start_screen() -> str:
        """Allow a temporary launch switch via CLI flag or env for previewing screens."""
        cli_args = sys.argv[1:]
        start_screen = None

        # Support --screen=<name> or --start-screen=<name>
        for arg in cli_args:
            if arg.startswith("--screen="):
                start_screen = arg.split("=", 1)[1]
                break
            if arg.startswith("--start-screen="):
                start_screen = arg.split("=", 1)[1]
                break

        # Support separated flag form: --screen home
        if not start_screen:
            for idx, arg in enumerate(cli_args):
                if arg in ("--screen", "--start-screen") and idx + 1 < len(cli_args):
                    start_screen = cli_args[idx + 1]
                    break

        # Fallback to env var
        if not start_screen:
            start_screen = os.environ.get("TUI_START_SCREEN", "menu")

        return (start_screen or "menu").lower()

    def on_mount(self) -> None:
        # Share a single DownloadManager across all screens to avoid races.
        self.download_manager = DownloadManager()

        # Track theme for runtime switching.
        self._theme_name = os.environ.get("TUI_THEME", "dark")

        # Seed the current console from cached metadata if available.
        cached = list_cached_consoles()
        if cached:
            first = cached[0]
            self.current_manufacturer = first["manufacturer"]
            self.current_console = first["console"]
            self.current_roms_path = first["roms_path"]
            self.current_manufacturer_slug = first["manufacturer_slug"]
            self.current_console_slug = first["console_slug"]
            self.current_module_guid = first.get("guid")
        else:
            # Defaults match the values used by the CLI.
            self.current_manufacturer = "Sega"
            self.current_console = "Dreamcast"
            self.current_roms_path = None
            self.current_manufacturer_slug = manufacturer_slug(self.current_manufacturer)
            self.current_console_slug = console_slug(self.current_console)
            self.current_module_guid = None

        # Allow previewing alternate landing screens without changing defaults.
        start_screen = self._get_start_screen()
        if start_screen == "home":
            self.push_screen(HomeScreen())
        elif start_screen == "bagels":
            self.push_screen(BagelsPreviewScreen())
        else:
            self.push_screen(MenuScreen("Main Menu"))

    def action_cycle_theme(self) -> None:
        """Cycle through available themes."""
        names = list(self.THEMES.keys())
        try:
            idx = names.index(self._theme_name)
        except ValueError:
            idx = 0
        next_theme = names[(idx + 1) % len(names)]
        self._theme_name = next_theme
        self.refresh_css()
        self.notify(f"Theme set to {next_theme}", title="Theme")


if __name__ == "__main__":
    ROMManagerApp().run()
