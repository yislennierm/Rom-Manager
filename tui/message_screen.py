from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static
from textual.screen import Screen
from textual import events

class MessageScreen(Screen):
    def __init__(self, title: str, message: str):
        super().__init__(id=title.replace(" ", "_"))
        self.title = title
        self.message = message

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"[b]{self.title}[/b]\n{self.message}\n\nPress ESC to return.", expand=True)
        yield Footer()

    def on_key(self, event: events.Key):
        if event.key in ("escape", "backspace"):
            self.app.pop_screen()
