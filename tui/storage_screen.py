from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Container
from textual.screen import Screen


class StorageScreen(Screen):
    """Placeholder storage management screen."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(
                "[b]Storage Management[/b]\n\n"
                "This section will surface cache usage, download directories, and cleanup tools.\n"
                "For now, manage downloads manually under the `downloads/` folder.\n",
                expand=True,
            ),
        )
        yield Footer()

    def action_go_back(self):
        self.app.pop_screen()
