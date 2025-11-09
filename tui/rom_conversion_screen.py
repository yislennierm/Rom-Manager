import os
from pathlib import Path
from typing import Iterable

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, Tree
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual import events


class ROMConversionScreen(Screen):
    """Workspace for experimenting with ROM conversion helpers."""

    DEFAULT_BASE = Path("downloads")

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("b", "go_back", "Back"),
        ("r", "refresh_tree", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Vertical(
                Static(
                    "ROM Conversion Lab — navigate the filesystem, inspect archives, and queue conversions.",
                    id="conversion_intro",
                ),
                Horizontal(
                    Tree("Workspace", id="conversion_tree"),
                    DataTable(id="conversion_detail"),
                    id="conversion_split",
                ),
                Static("Select a file to view details. Actions coming soon…", id="conversion_status"),
                id="conversion_panel",
            ),
            id="conversion_container",
        )
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self):
        self._tree = self.query_one("#conversion_tree", Tree)
        self._detail = self.query_one("#conversion_detail", DataTable)
        self._detail.add_columns("Property", "Value")
        self._base_path = self.DEFAULT_BASE if self.DEFAULT_BASE.exists() else Path.cwd()
        self._populate_root()

    # ------------------------------------------------------------------

    def _populate_root(self):
        root_label = str(self._base_path.resolve())
        root_node = self._tree.root
        if hasattr(root_node, "set_label"):
            root_node.set_label(root_label)
        else:
            root_node.label = root_label
        root_node.data = self._base_path
        root_node.remove_children()
        self._expand_node(root_node)
        root_node.expand()
        self._tree.focus()

    def _expand_node(self, node):
        path = node.data
        if not isinstance(path, Path) or not path.is_dir():
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            node.add("[Permission denied]", allow_expand=False)
            return
        for child in entries:
            label = f"{child.name}/" if child.is_dir() else child.name
            child_node = node.add(label, data=child, allow_expand=child.is_dir())
            if child.is_dir():
                # Add a placeholder so the expand arrow shows.
                child_node.add("…", allow_expand=False)

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        path = event.node.data
        if isinstance(path, Path):
            self._show_details(path)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded):
        node = event.node
        # Remove placeholder children and repopulate with actual entries.
        node.remove_children()
        self._expand_node(node)

    def _show_details(self, path: Path):
        self._detail.clear()
        if not path.exists():
            self._detail.add_row("Status", "Missing")
            return
        stats = path.stat()
        rows = [
            ("Name", path.name or str(path)),
            ("Type", "Directory" if path.is_dir() else path.suffix or "File"),
            ("Size", f"{stats.st_size:,} bytes"),
            ("Modified", self._format_timestamp(stats.st_mtime)),
            ("Absolute Path", str(path.resolve())),
        ]
        if path.is_file():
            rows.append(("Preview", "Select to convert / extract (todo)"))
        for key, value in rows:
            self._detail.add_row(key, value)

    @staticmethod
    def _format_timestamp(epoch: float) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(epoch).isoformat(sep=" ", timespec="seconds")

    # ------------------------------------------------------------------
    # Actions / key bindings
    # ------------------------------------------------------------------

    def action_go_back(self):
        self.app.pop_screen()

    def action_refresh_tree(self):
        self._populate_root()

    def on_key(self, event: events.Key) -> None:
        tree = getattr(self, "_tree", None)
        node = tree.cursor_node if tree else None
        if event.key == "enter" and node and isinstance(node.data, Path):
            path = node.data
            if path.is_dir():
                node.expand()
            else:
                self._show_details(path)
            event.stop()
            return
        return
