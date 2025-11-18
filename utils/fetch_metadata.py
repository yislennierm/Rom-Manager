#!/usr/bin/env python3
import argparse
import sys
from typing import Optional

from core.services.provider_tasks import fetch_console_metadata as run_fetch


# Backward-compatible wrapper so other modules can import this function.
def fetch_console_metadata(
    console: str,
    manufacturer: Optional[str],
    provider_slug: Optional[str] = None,
    force: bool = False,
):
    return run_fetch(console=console, manufacturer=manufacturer, provider_slug=provider_slug, force=force)


def main():
    parser = argparse.ArgumentParser(description="Fetch provider metadata assets for a console.")
    parser.add_argument("--console", default="Dreamcast", help="Console name to fetch")
    parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    parser.add_argument("--force", action="store_true", help="Redownload assets even if they exist")

    args = parser.parse_args()

    try:
        result = fetch_console_metadata(args.console, args.manufacturer, force=args.force)
    except Exception as exc:
        print(f"❌ Fetch failed: {exc}")
        sys.exit(1)

    print("\n📦 Cached assets:")
    for key, value in result.items():
        print(f"  - {key}: {value}")


if __name__ == "__main__":
    main()
