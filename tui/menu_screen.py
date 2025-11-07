from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Container, Vertical
from textual.screen import Screen

from .console_select_screen import ConsoleSelectScreen
from .download_manager_screen import DownloadManagerScreen
from .global_search_screen import GlobalSearchScreen
from .message_screen import MessageScreen
from .database_screen import DatabaseScreen
from .rom_explorer_screen import ROMExplorerScreen
from .settings_screen import SettingsScreen
from .storage_screen import StorageScreen


MENU_STRUCTURE = {
    "Main Menu": ["ROM Explorer", "Download Manager", "Settings", "Exit"],
    "ROM Explorer": ["Consoles", "Computers", "Search", "Back"],
    "Settings": ["Providers", "Storage", "Database", "Back"],
}


class MenuScreen(Screen):
    """Generic navigation menu screen."""

    BINDINGS = [
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("enter", "select_option", "Select"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def __init__(self, name: str):
        safe_id = name.replace(" ", "_")
        super().__init__(id=safe_id)
        self.menu_name = name
        self.options = MENU_STRUCTURE.get(name, [])
        self.cursor = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(f"[b]{self.menu_name}[/b]\n", id="menu_title"),
            Vertical(id="menu_list"),
        )
        yield Footer()

    def on_mount(self):
        self.menu_list = self.query_one("#menu_list")
        self.refresh_menu()

    def refresh_menu(self):
        self.menu_list.remove_children()
        for i, item in enumerate(self.options):
            prefix = "👉 " if i == self.cursor else "   "
            text = f"{prefix}[bold yellow]{item}[/bold yellow]" if i == self.cursor else f"{prefix}{item}"
            self.menu_list.mount(Static(text))

    def select_option(self, option: str):
        """Handle navigation between menu items."""
        if option in MENU_STRUCTURE:
            self.app.push_screen(MenuScreen(option))
        elif option == "Consoles":
            self.app.push_screen(ConsoleSelectScreen())
        elif option == "Computers":
            self.app.push_screen(MessageScreen("Computers", "Computer collections will be available soon."))
        elif option == "Search":
            self.app.push_screen(GlobalSearchScreen())
        elif option == "Download Manager":
            self.app.push_screen(DownloadManagerScreen())
        elif option == "Providers":
            self.app.push_screen(SettingsScreen())
        elif option == "Storage":
            self.app.push_screen(StorageScreen())
        elif option == "Database":
            self.app.push_screen(DatabaseScreen())
        elif option == "Back":
            self.app.pop_screen()
        elif option == "Exit":
            self.app.exit()
        else:
            self.app.push_screen(MessageScreen(option, f"Selected: {option}"))

    # ------------------------------------------------------------------
    # Actions for key bindings
    # ------------------------------------------------------------------

    def action_move_up(self):
        if not self.options:
            return
        self.cursor = (self.cursor - 1) % len(self.options)
        self.refresh_menu()

    def action_move_down(self):
        if not self.options:
            return
        self.cursor = (self.cursor + 1) % len(self.options)
        self.refresh_menu()

    def action_select_option(self):
        if not self.options:
            return
        self.select_option(self.options[self.cursor])

    def action_go_back(self):
        if self.menu_name != "Main Menu":
            self.app.pop_screen()
