from typing import Dict, List, Tuple

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Input, Button, Static, Select
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual import events

from core.providers import add_provider
from utils.library_sync import load_modules
from .message_screen import MessageScreen

REQUIRED_FIELDS = [
    "manufacturer",
    "console",
    "name",
    "provider",
    "archive_id",
    "base_url",
    "meta_sqlite",
]


class ProviderFormScreen(Screen):
    """Form to collect a new provider entry."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Save"),
    ]

    def __init__(self, on_save=None):
        super().__init__()
        self.on_save = on_save
        self.selected_guid: str | None = None
        self.module_options: List[Tuple[str, str | None]] = []
        self.module_lookup: Dict[str, Dict] = {}

    def compose(self) -> ComposeResult:
        self.module_options = self._module_select_options()
        yield Header()
        yield Container(
            VerticalScroll(
                Static("[b]Add Provider[/b]\nProvide metadata and press Ctrl+S to save.\n", id="form_title"),
                Select(self.module_options, prompt="Select console…", id="module_select"),
                Input(placeholder="Manufacturer (e.g., Sega)", id="manufacturer"),
                Input(placeholder="Console (e.g., Game Gear)", id="console"),
                Input(placeholder="Libretro GUID (auto-filled)", id="libretro_guid", disabled=True),
                Input(placeholder="Display name (e.g., Sega Game Gear ROMSet Ultra)", id="name"),
                Input(placeholder="Provider (e.g., Internet Archive)", id="provider"),
                Input(placeholder="Archive ID (e.g., sega-game-gear-romset-ultra-us)", id="archive_id"),
                Input(placeholder="Base URL (e.g., https://archive.org/download/...)", id="base_url"),
                Input(placeholder="Meta SQLite URL", id="meta_sqlite"),
                Input(placeholder="Files XML URL (optional)", id="files_xml"),
                Input(placeholder="Torrent URL (optional)", id="torrent"),
                Input(placeholder="Meta XML URL (optional)", id="meta_xml"),
                Input(placeholder="Reviews XML URL (optional)", id="reviews_xml"),
                Input(placeholder="ROM extensions (comma separated, e.g., .gg,.sms)", id="rom_extensions"),
                Input(placeholder="Size label (optional)", id="size"),
                Input(placeholder="Updated date YYYY-MM-DD (optional)", id="updated"),
                Button("Save (Ctrl+S)", id="save_button"),
                Button("Cancel (Esc)", id="cancel_button"),
                id="form_container",
            )
        )
        yield Footer()

    @property
    def _fields(self):
        return {
            "manufacturer": self.query_one("#manufacturer", Input),
            "console": self.query_one("#console", Input),
            "name": self.query_one("#name", Input),
            "provider": self.query_one("#provider", Input),
            "archive_id": self.query_one("#archive_id", Input),
            "base_url": self.query_one("#base_url", Input),
            "meta_sqlite": self.query_one("#meta_sqlite", Input),
            "files_xml": self.query_one("#files_xml", Input),
            "torrent": self.query_one("#torrent", Input),
            "meta_xml": self.query_one("#meta_xml", Input),
            "reviews_xml": self.query_one("#reviews_xml", Input),
            "rom_extensions": self.query_one("#rom_extensions", Input),
            "size": self.query_one("#size", Input),
            "updated": self.query_one("#updated", Input),
            "libretro_guid": self.query_one("#libretro_guid", Input),
        }

    def action_cancel(self):
        self.app.pop_screen()

    def action_submit(self):
        data = {key: field.value.strip() for key, field in self._fields.items()}
        missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
        if missing:
            self._notify(f"Missing required fields: {', '.join(missing)}", severity="error")
            return

        entry = {
            "name": data["name"],
            "provider": data["provider"],
            "archive_id": data["archive_id"],
            "base_url": data["base_url"],
            "files": {
                "meta_sqlite": data["meta_sqlite"],
            },
        }
        if data["files_xml"]:
            entry["files"]["files_xml"] = data["files_xml"]
        if data["torrent"]:
            entry["files"]["torrent"] = data["torrent"]
        if data["meta_xml"]:
            entry["files"]["meta_xml"] = data["meta_xml"]
        if data["reviews_xml"]:
            entry["files"]["reviews_xml"] = data["reviews_xml"]
        if data["rom_extensions"]:
            extensions = [ext.strip() for ext in data["rom_extensions"].split(",") if ext.strip()]
            extensions = [ext if ext.startswith(".") else f".{ext}" for ext in extensions]
            entry["rom_extensions"] = extensions
        if data["size"]:
            entry["size"] = data["size"]
        if data["updated"]:
            entry["updated"] = data["updated"]
        guid = data.get("libretro_guid") or self.selected_guid
        if guid:
            entry["libretro_guid"] = guid
        try:
            add_provider(data["manufacturer"], data["console"], entry)
        except Exception as exc:
            self._notify(f"Failed to add provider: {exc}", severity="error")
            self.app.push_screen(MessageScreen("Add Provider Failed", str(exc)))
            return

        self._notify(f"Added provider {data['manufacturer']}/{data['console']}", severity="success")
        if callable(self.on_save):
            self.on_save()
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save_button":
            self.action_submit()
        elif event.button.id == "cancel_button":
            self.action_cancel()

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter" and event.sender.id == "save_button":
            self.action_submit()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "module_select":
            return
        value = event.value
        if not value:
            return
        module = self.module_lookup.get(value)
        if module:
            self._apply_module(module)

    def _apply_module(self, module: Dict) -> None:
        manufacturer, console = self._split_name(module.get("name"))
        self._fields["manufacturer"].value = manufacturer
        self._fields["console"].value = console
        guid = module.get("guid")
        if guid:
            self.selected_guid = guid
            self._fields["libretro_guid"].value = guid

    def _split_name(self, name: str | None) -> tuple[str, str]:
        if not name:
            return ("Unknown", "Unknown")
        parts = [segment.strip() for segment in name.split("-", 1)]
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], parts[-1]

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")

    def _module_select_options(self) -> List[Tuple[str, str | None]]:
        modules = load_modules()
        options: List[Tuple[str, str | None]] = [("Select console…", None)]
        self.module_lookup = {}
        for module in modules:
            guid = module.get("guid")
            if not guid:
                continue
            label = module.get("name") or guid
            options.append((label, guid))
            self.module_lookup[guid] = module
        return options
