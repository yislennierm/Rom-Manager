from __future__ import annotations

import configparser
from pathlib import Path
from typing import Dict, Optional


IA_CONFIG_CANDIDATES = (
    Path.home() / ".config" / "internetarchive" / "ia.ini",
    Path.home() / ".config" / "ia.ini",
    Path.home() / ".ia",
)


def ia_config_path() -> Optional[Path]:
    for path in IA_CONFIG_CANDIDATES:
        if path.exists():
            return path
    return None


def ia_cookie_header() -> Optional[str]:
    path = ia_config_path()
    if not path:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path)
    except configparser.Error:
        return None
    if not parser.has_section("cookies"):
        return None
    parts = []
    for name, value in parser.items("cookies"):
        value = value.split(";", 1)[0].strip()
        if value:
            parts.append(f"{name}={value}")
    return "; ".join(parts) if parts else None


def ia_auth_status() -> Dict[str, object]:
    path = ia_config_path()
    cookie = ia_cookie_header()
    return {
        "configured": bool(cookie),
        "config_found": bool(path),
        "config_path": str(path) if path else None,
        "method": "internetarchive ia.ini cookies" if cookie else None,
    }


def headers_for_url(url: str) -> Dict[str, str]:
    headers = {"User-Agent": "ROMs-Manager/1.0"}
    if "archive.org/" not in url.lower():
        return headers
    cookie = ia_cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    return headers
