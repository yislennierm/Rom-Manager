from textual.app import App

from core.download_manager import DownloadManager
from utils.paths import list_cached_consoles, manufacturer_slug, console_slug
from .menu_screen import MenuScreen


class ROMManagerApp(App):
    TITLE = "ROMs Manager"
    SUB_TITLE = "TUI Interface"

    def on_mount(self) -> None:
        # Share a single DownloadManager across all screens to avoid races.
        self.download_manager = DownloadManager()

        # Seed the current console from cached metadata if available.
        cached = list_cached_consoles()
        if cached:
            first = cached[0]
            self.current_manufacturer = first["manufacturer"]
            self.current_console = first["console"]
            self.current_roms_path = first["roms_path"]
            self.current_manufacturer_slug = first["manufacturer_slug"]
            self.current_console_slug = first["console_slug"]
        else:
            # Defaults match the values used by the CLI.
            self.current_manufacturer = "Sega"
            self.current_console = "Dreamcast"
            self.current_roms_path = None
            self.current_manufacturer_slug = manufacturer_slug(self.current_manufacturer)
            self.current_console_slug = console_slug(self.current_console)

        self.push_screen(MenuScreen("Main Menu"))


if __name__ == "__main__":
    ROMManagerApp().run()
