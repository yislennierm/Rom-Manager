from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static, Tabs, Tab


class BagelsPreviewScreen(Screen):
    """Static Bagels-inspired home preview using dummy data."""

    CSS_PATH = "styles/bagels_preview.css"

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("↪ Bagels 0.3.12", id="bp_title"),
            Tabs(Tab("Home"), Tab("Manager"), id="bp_tabs"),
            Static("instance", id="bp_instance"),
            id="bp_header",
        )

        # Left column panels
        left = Vertical(
            self._panel("Accounts", "A…\n0\nS", "bp_accounts"),
            self._panel("View and add", "[red]Expense[/red]   Income", "bp_modes"),
            self._panel("Period", self._calendar_text(), "bp_calendar"),
            self._panel(
                "Insights",
                "Expense of This W   0\nExpense per day   0.0\n\n///// No data to display /////",
                "bp_insights",
            ),
            id="bp_left",
        )

        # Right column panels
        right = Vertical(
            self._panel("Templates", "• My template", "bp_templates"),
            self._panel("Records", self._records_text(), "bp_records"),
            id="bp_right",
        )

        yield Horizontal(left, right, id="bp_body")
        yield Footer()

    @staticmethod
    def _panel(title: str, body: str, pid: str) -> Container:
        return Container(
            Static(title, classes="bp_panel_title"),
            Static(body, id=pid, classes="bp_panel_body"),
            classes="bp_panel",
        )

    @staticmethod
    def _calendar_text() -> str:
        return "\n".join(
            [
                "Period",
                "<<<   This Week   >>>",
                "S  M  T  W  T  F  S",
                "30  1  2  3  4  5  6",
                "[red]7[/red]   8  9 10 11 12 13",
                "14 15 16 17 18 19 20",
                "21 22 23 24 25 26 27",
                "28 29 30 31  1  2  3",
            ]
        )

    @staticmethod
    def _records_text() -> str:
        return (
            "Records\n"
            "Date (q)                Person (w)\n"
            "Filter categ  Filter amount  Filter label\n\n"
            "[dim]No entries[/dim]"
        )
