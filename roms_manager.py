#!/usr/bin/env python3
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from jsonschema import ValidationError, validate

from core.providers import (
    add_provider,
    entry_provider_slug,
    load_cached_roms,
    list_providers_with_status,
    load_providers,
    remove_provider,
    resolve_system,
    validate_providers_schema,
)
from utils.library_sync import sync_modules, build_module_index, load_modules, index_exists
from utils.paths import (
    PROVIDER_FILE,
    SCHEMA_FILE,
    console_dirs,
    files_xml_path,
    metadata_file_path,
    path_prefix,
    roms_json_path,
    torrent_file_path,
)

def _filename_from_url(url: Optional[str], fallback: str) -> str:
    if not url:
        return fallback
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name or fallback


def _download_file(url: str, destination: str, label: str) -> None:
    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        print(f"⬇️ Downloading {label} …")
        urllib.request.urlretrieve(url, destination)
        print(f"✅ Saved to {destination}")
    except (HTTPError, URLError) as err:
        if os.path.exists(destination):
            os.remove(destination)
        print(f"❌ Failed to download {label}: {err}")
        raise


def _resolve_console_provider(
    console: str,
    manufacturer: Optional[str] = None,
    provider: Optional[str] = None,
):
    providers = load_providers()
    try:
        manufacturer_key, system = resolve_system(console, manufacturer, providers, provider)
    except KeyError as err:
        raise

    console_entries = (
        providers.get("console_root", {})
        .get(manufacturer_key, {})
        .get(console)
    )

    if isinstance(console_entries, list):
        variants = [variant for variant in console_entries if isinstance(variant, dict)]
        if not variants:
            raise ValueError(f"No provider definitions found for {manufacturer_key}/{console}.")
        if provider is None and len(variants) > 1:
            choices = []
            for variant in variants:
                slug_value = entry_provider_slug(variant)
                label = (
                    variant.get("provider")
                    or variant.get("name")
                    or variant.get("archive_id")
                    or slug_value
                )
                choices.append(f"{slug_value} ({label})")
            raise ValueError(
                f"Multiple providers configured for {manufacturer_key}/{console}. "
                f"Specify --provider with one of: {', '.join(choices)}."
            )

    slug_value = entry_provider_slug(system)
    return manufacturer_key, system, slug_value


def _roms_json_with_fallback(manufacturer: str, console: str, provider_slug: Optional[str]) -> str:
    if provider_slug:
        path = roms_json_path(manufacturer, console, provider_slug)
        if os.path.exists(path):
            return path
        legacy_path = roms_json_path(manufacturer, console)
        if os.path.exists(legacy_path):
            return legacy_path
        return path
    return roms_json_path(manufacturer, console)


def _asset_path_with_fallback(
    resolver,
    manufacturer: str,
    console: str,
    filename: Optional[str],
    provider_slug: Optional[str],
):
    if filename is None:
        return None
    path = resolver(manufacturer, console, filename, provider_slug)
    if provider_slug and not os.path.exists(path):
        legacy_path = resolver(manufacturer, console, filename)
        if os.path.exists(legacy_path):
            return legacy_path
    return path


# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------

def cmd_validate():
    """Validate providers.json against its schema."""
    try:
        providers = load_providers()
        with open(SCHEMA_FILE) as f:
            schema = json.load(f)
    except FileNotFoundError as e:
        print(f"❌ Missing file: {e.filename}")
        sys.exit(1)

    try:
        validate(instance=providers, schema=schema)
        print("✅ providers.json is valid.")
    except ValidationError as e:
        print("❌ Validation error:")
        print("Path:", list(e.path))
        print("Message:", e.message)
        sys.exit(1)


def cmd_fetch(console="Dreamcast", manufacturer=None, provider=None):
    """Download metadata files for a console (SQLite + XML + torrent)."""
    try:
        manufacturer_key, system, slug_value = _resolve_console_provider(console, manufacturer, provider)
    except KeyError as err:
        print(f"❌ {err}")
        sys.exit(1)
    except ValueError as err:
        print(f"❌ {err}")
        sys.exit(1)

    files = system["files"]
    prefix = path_prefix(manufacturer_key, console, slug_value)
    console_dirs(manufacturer_key, console, slug_value, ensure=True)

    meta_url = files.get("meta_sqlite")
    if not meta_url:
        print("❌ Provider entry does not specify a meta_sqlite URL.")
        sys.exit(1)
    meta_filename = _filename_from_url(meta_url, f"{prefix}_meta.sqlite")
    meta_path = metadata_file_path(manufacturer_key, console, meta_filename, slug_value)

    files_xml_url = files.get("files_xml")
    torrent_url = files.get("torrent")

    if not os.path.exists(meta_path):
        try:
            _download_file(meta_url, meta_path, f"{console} metadata DB")
        except Exception:
            sys.exit(1)
    else:
        print(f"✅ DB already exists: {meta_path}")

    if files_xml_url:
        xml_filename = _filename_from_url(files_xml_url, f"{prefix}_files.xml")
        xml_path = files_xml_path(manufacturer_key, console, xml_filename, slug_value)
        if not os.path.exists(xml_path):
            try:
                _download_file(files_xml_url, xml_path, f"{console} file listing XML")
            except Exception:
                sys.exit(1)
        else:
            print(f"✅ XML already exists: {xml_path}")
    else:
        print("⚠️ No files_xml URL provided for this console.")

    if torrent_url:
        torrent_filename = _filename_from_url(torrent_url, f"{prefix}_archive.torrent")
        torrent_path = torrent_file_path(manufacturer_key, console, torrent_filename, slug_value)
        if not os.path.exists(torrent_path):
            try:
                _download_file(torrent_url, torrent_path, f"{console} torrent file")
            except Exception:
                sys.exit(1)
        else:
            print(f"✅ Torrent already exists: {torrent_path}")
    else:
        print("⚠️ No torrent URL found in provider data.")

    print("✅ All metadata ready.")


def cmd_explore(console="Dreamcast", manufacturer=None, provider=None, export_json=False):
    """Explore metadata (list ROM files and sizes, optional JSON export)."""
    try:
        manufacturer_key, system, slug_value = _resolve_console_provider(console, manufacturer, provider)
    except KeyError as err:
        print(f"❌ {err}")
        return
    except ValueError as err:
        print(f"❌ {err}")
        return

    files = system.get("files", {})
    prefix = path_prefix(manufacturer_key, console, slug_value)

    torrent_url = files.get("torrent")

    meta_filename = None
    meta_url = files.get("meta_sqlite")
    if meta_url:
        meta_filename = _filename_from_url(meta_url, f"{prefix}_meta.sqlite")
    db_path = _asset_path_with_fallback(metadata_file_path, manufacturer_key, console, meta_filename, slug_value)

    xml_filename = None
    files_xml_url = files.get("files_xml")
    if files_xml_url:
        xml_filename = _filename_from_url(files_xml_url, f"{prefix}_files.xml")
    xml_path = _asset_path_with_fallback(files_xml_path, manufacturer_key, console, xml_filename, slug_value)

    # Try SQLite first
    if db_path and os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cur.fetchall()]
        print("📋 Tables:")
        for t in tables:
            print("  -", t)

        if "files" in tables:
            print("\n🎮 Sample ROM entries (from SQLite):")
            for row in cur.execute("SELECT name, size FROM files LIMIT 10;"):
                print(f"  {row[0]} ({row[1]} bytes)")
            conn.close()
            return
        conn.close()

    # Fall back to XML
    if not xml_path or not os.path.exists(xml_path):
        print("❌ No metadata source found. Run `fetch` first.")
        return

    print("\n📂 Reading from XML file list...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    extensions = system.get("rom_extensions", [])
    if not extensions:
        extensions = [".zip", ".bin"]

    roms = []
    archive_extensions = [".zip", ".7z", ".rar"]

    for f in root.findall("file"):
        name = f.get("name")
        if not name:
            continue

        name_lower = name.lower()
        if not any(name_lower.endswith(ext.lower()) for ext in extensions):
            if not any(name_lower.endswith(ext) for ext in archive_extensions):
                continue

        roms.append({
            "name": name,
            "size": f.get("size"),
            "md5": f.findtext("md5"),
            "crc32": f.findtext("crc32"),
            "sha1": f.findtext("sha1"),
            "console": console,
            "manufacturer": manufacturer_key,
            "torrent_url": torrent_url,
        })

    print(f"🎮 Found {len(roms)} ROMs matching extensions {extensions}.")
    for rom in roms[:10]:
        print(f"  {rom['name']} ({rom['size']} bytes)")

    # Optionally export to JSON
    if export_json:
        json_path = roms_json_path(manufacturer_key, console, slug_value)
        with open(json_path, "w") as out:
            json.dump(roms, out, indent=2)
        print(f"\n💾 Exported {len(roms)} entries to {json_path}")


def load_roms(console="Dreamcast", manufacturer=None, provider=None):
    try:
        manufacturer_key, _, slug_value = _resolve_console_provider(console, manufacturer, provider)
    except KeyError as err:
        print(f"❌ {err}")
        sys.exit(1)
    except ValueError as err:
        print(f"❌ {err}")
        sys.exit(1)

    json_path = _roms_json_with_fallback(manufacturer_key, console, slug_value)
    if not os.path.exists(json_path):
        print(f"❌ No ROM list found for {manufacturer_key} {console}. Run `explore --json` first.")
        sys.exit(1)
    with open(json_path) as f:
        return json.load(f)


def cmd_list(console="Dreamcast", manufacturer=None, provider=None, limit=20):
    roms = load_roms(console, manufacturer, provider=provider)
    print(f"📜 Showing first {limit} ROMs ({len(roms)} total):\n")
    for r in roms[:limit]:
        print(f"  {r['name']} ({r['size']} bytes)")


def cmd_search(query, console="Dreamcast", manufacturer=None, provider=None, global_search=False):
    if global_search:
        if provider:
            print("⚠️ Ignoring --provider because --global was used.")
        roms = load_cached_roms()
    else:
        roms = load_roms(console, manufacturer, provider=provider)
    matches = [r for r in roms if query.lower() in r["name"].lower()]
    print(f"🔍 Found {len(matches)} matches for '{query}':\n")
    for r in matches[:20]:
        console_label = r.get("console", console if not global_search else "Unknown")
        manufacturer_label = r.get("manufacturer", manufacturer if not global_search else "Unknown")
        size = r.get("size")
        print(f"  {r['name']} — {manufacturer_label}/{console_label} ({size} bytes)")


# -------------------------------------------------------------------
# Provider management
# -------------------------------------------------------------------

def cmd_providers_list():
    providers = list_providers_with_status()
    if not providers:
        print("⚠️ No providers defined. Use `providers add` to create one.")
        return

    for item in providers:
        status = item["status"]
        files = []
        files.append("metadata" if status.get("metadata") else "metadata❌")
        files.append("listings" if status.get("listings") else "listings❌")
        files.append("rom_json" if status.get("rom_json") else "rom_json❌")
        files.append("torrent" if status.get("torrent") else "torrent❌")
        print(f"- {item['manufacturer']} / {item['console']}")
        print(f"    Name: {item['entry'].get('name')}")
        print(f"    Provider: {item['entry'].get('provider')}")
        print(f"    Archive ID: {item['entry'].get('archive_id')}")
        print(f"    ROM extensions: {', '.join(item['rom_extensions']) or '—'}")
        print(f"    Cached assets: {', '.join(files)}")
        print()


def cmd_providers_add(args):
    extensions = []
    if args.rom_extensions:
        extensions = [ext.strip() for ext in args.rom_extensions.split(",") if ext.strip()]
        extensions = [ext if ext.startswith(".") else f".{ext}" for ext in extensions]

    files_entry = {
        "meta_sqlite": args.meta_sqlite,
    }
    if args.files_xml:
        files_entry["files_xml"] = args.files_xml
    if args.torrent:
        files_entry["torrent"] = args.torrent
    if args.meta_xml:
        files_entry["meta_xml"] = args.meta_xml
    if args.reviews_xml:
        files_entry["reviews_xml"] = args.reviews_xml

    entry = {
        "name": args.name,
        "provider": args.provider_name,
        "archive_id": args.archive_id,
        "base_url": args.base_url,
        "files": files_entry,
    }
    if extensions:
        entry["rom_extensions"] = extensions
    if args.size:
        entry["size"] = args.size
    if args.updated:
        entry["updated"] = args.updated

    try:
        add_provider(args.manufacturer, args.console, entry, overwrite=args.force)
        print(f"✅ Added provider {args.manufacturer}/{args.console}.")
    except Exception as exc:
        print(f"❌ Failed to add provider: {exc}")
        sys.exit(1)


def cmd_providers_remove(args):
    try:
        remove_provider(
            args.manufacturer,
            args.console,
            provider_slug=args.provider,
            remove_cache=args.purge_cache,
        )
        suffix = f" ({args.provider})" if args.provider else ""
        print(f"🗑️ Removed provider {args.manufacturer}/{args.console}{suffix}.")
    except Exception as exc:
        print(f"❌ Failed to remove provider: {exc}")
        sys.exit(1)


def cmd_database_fetch(args):
    token = os.environ.get("GITHUB_TOKEN")
    requested = [name.strip() for name in (args.module or []) if name and name.strip()]
    try:
        modules = sync_modules(token, names=requested if requested else None)
    except Exception as exc:
        print(f"❌ Failed to fetch module list: {exc}")
        sys.exit(1)

    if requested:
        synced = {m.get("name") for m in modules}
        missing = [name for name in requested if name not in synced]
        for name in missing:
            print(f"⚠️ Module {name} not found in libretro repo snapshot.")

    count = len(modules)
    if count == 0:
        print("⚠️ No modules stored. Specify valid module names or run without --module to sync all consoles.")
        return

    noun = "definition" if count == 1 else "definitions"
    print(f"✅ Synced {count} module {noun}.")
    print("ℹ️ Activate consoles with `python3 roms_manager.py database activate --module \"<name>\"` to build indexes.")


def cmd_database_activate(args):
    requested = [name.strip() for name in (args.module or []) if name and name.strip()]
    if not requested:
        print("⚠️ Provide at least one --module value to activate.")
        return

    modules = load_modules()
    if not modules:
        print("⚠️ No module definitions found. Run `python3 roms_manager.py database fetch` first.")
        return

    available = {m.get("name") for m in modules}
    missing = [name for name in requested if name not in available]
    for name in missing:
        print(f"⚠️ Module {name} is not in the synced list. Run database fetch if you need it.")

    token = os.environ.get("GITHUB_TOKEN")
    for name in requested:
        if name in missing:
            continue
        if index_exists(name) and not args.force:
            print(f"⏭️ Skipping {name}; index already exists (use --force to rebuild).")
            continue
        try:
            path = build_module_index(name, token)
            print(f"✅ Indexed {name}: {path}")
        except Exception as exc:
            print(f"⚠️ Failed to index {name}: {exc}")


# -------------------------------------------------------------------
# Main CLI
# -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ROMs Manager CLI — manage and explore ROM metadata"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Validate providers.json structure")

    fetch_parser = sub.add_parser("fetch", help="Download metadata and torrent files")
    fetch_parser.add_argument("--console", default="Dreamcast", help="Console name")
    fetch_parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    fetch_parser.add_argument(
        "--provider",
        help="Provider slug/archive ID when multiple providers exist for a console",
    )

    explore_parser = sub.add_parser("explore", help="Explore metadata contents")
    explore_parser.add_argument("--console", default="Dreamcast", help="Console name")
    explore_parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    explore_parser.add_argument(
        "--provider",
        help="Provider slug/archive ID when multiple providers exist for a console",
    )
    explore_parser.add_argument("--json", action="store_true", help="Export ROM list to JSON")

    list_parser = sub.add_parser("list", help="List ROMs from local JSON")
    list_parser.add_argument("--console", default="Dreamcast", help="Console name")
    list_parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    list_parser.add_argument(
        "--provider",
        help="Provider slug/archive ID when multiple providers exist for a console",
    )
    list_parser.add_argument("--limit", type=int, default=20, help="Number of entries to show")

    search_parser = sub.add_parser("search", help="Search ROMs by name")
    search_parser.add_argument("query", help="Search term")
    search_parser.add_argument("--console", default="Dreamcast", help="Console name")
    search_parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    search_parser.add_argument(
        "--provider",
        help="Provider slug/archive ID; ignored when --global is supplied",
    )
    search_parser.add_argument("--global", action="store_true", dest="global_search", help="Search all cached consoles")

    database_parser = sub.add_parser("database", help="ROM database utilities")
    database_sub = database_parser.add_subparsers(dest="database_command", required=True)

    database_fetch_parser = database_sub.add_parser("fetch", help="Sync libretro module list")
    database_fetch_parser.add_argument(
        "--module",
        action="append",
        help="Limit sync to specific module name(s); may be passed multiple times",
    )
    database_activate_parser = database_sub.add_parser(
        "activate", help="Build artwork indexes for synced modules"
    )
    database_activate_parser.add_argument(
        "--module",
        action="append",
        required=True,
        help="Module name to activate (repeat for multiple consoles)",
    )
    database_activate_parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild the index even if a cached version already exists",
    )
    providers_parser = sub.add_parser("providers", help="Manage provider definitions")
    providers_sub = providers_parser.add_subparsers(dest="providers_command", required=True)

    providers_sub.add_parser("list", help="List configured providers")

    provider_add_parser = providers_sub.add_parser("add", help="Add a new provider entry")
    provider_add_parser.add_argument("--manufacturer", required=True, help="Manufacturer name (e.g., Sega)")
    provider_add_parser.add_argument("--console", required=True, help="Console name (e.g., Game Gear)")
    provider_add_parser.add_argument("--name", required=True, help="Display name for this provider")
    provider_add_parser.add_argument("--provider-name", required=True, help="Data source provider (e.g., Internet Archive)")
    provider_add_parser.add_argument("--archive-id", required=True, help="Archive identifier slug")
    provider_add_parser.add_argument("--base-url", required=True, help="Base URL to the provider archive")
    provider_add_parser.add_argument("--meta-sqlite", required=True, help="URL to the metadata SQLite file")
    provider_add_parser.add_argument("--files-xml", help="URL to the files XML listing")
    provider_add_parser.add_argument("--torrent", help="URL to the torrent file")
    provider_add_parser.add_argument("--meta-xml", help="Optional metadata XML URL")
    provider_add_parser.add_argument("--reviews-xml", help="Optional reviews XML URL")
    provider_add_parser.add_argument("--rom-extensions", help="Comma-separated ROM extensions (e.g., .gg,.sms)")
    provider_add_parser.add_argument("--size", help="Optional archive size string")
    provider_add_parser.add_argument("--updated", help="Optional ISO date of last update")
    provider_add_parser.add_argument("--force", action="store_true", help="Overwrite existing provider entry if present")

    provider_remove_parser = providers_sub.add_parser("remove", help="Remove an existing provider")
    provider_remove_parser.add_argument("--manufacturer", required=True, help="Manufacturer name")
    provider_remove_parser.add_argument("--console", required=True, help="Console name")
    provider_remove_parser.add_argument(
        "--provider",
        help="Provider slug/archive ID when console has multiple providers",
    )
    provider_remove_parser.add_argument("--purge-cache", action="store_true", help="Delete cached assets for this provider")

    args = parser.parse_args()

    if args.command == "validate":
        cmd_validate()
    elif args.command == "fetch":
        cmd_fetch(console=args.console, manufacturer=args.manufacturer, provider=args.provider)
    elif args.command == "explore":
        cmd_explore(
            console=args.console,
            manufacturer=args.manufacturer,
            provider=args.provider,
            export_json=args.json,
        )
    elif args.command == "list":
        cmd_list(
            console=args.console,
            manufacturer=args.manufacturer,
            provider=args.provider,
            limit=args.limit,
        )
    elif args.command == "search":
        cmd_search(
            args.query,
            console=args.console,
            manufacturer=args.manufacturer,
            provider=args.provider,
            global_search=args.global_search,
        )
    elif args.command == "providers":
        if args.providers_command == "list":
            cmd_providers_list()
        elif args.providers_command == "add":
            cmd_providers_add(args)
        elif args.providers_command == "remove":
            cmd_providers_remove(args)
    elif args.command == "database":
        if args.database_command == "fetch":
            cmd_database_fetch(args)
        elif args.database_command == "activate":
            cmd_database_activate(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
