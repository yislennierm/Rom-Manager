import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PROVIDERS_FILE = os.path.join("data", "artwork", "providers.json")
INDEX_DIR = os.path.join("data", "artwork", "index")


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "default"


def load_artwork_providers() -> Dict:
    with open(PROVIDERS_FILE) as fh:
        data = json.load(fh)
    return data.get("providers", {})


def get_provider(provider_id: str) -> Dict:
    providers = load_artwork_providers()
    if provider_id not in providers:
        raise KeyError(f"Unknown artwork provider '{provider_id}'")
    return providers[provider_id]


def provider_console_path(provider: Dict, console: str) -> str:
    consoles = provider.get("consoles", {})
    if console not in consoles:
        raise KeyError(f"Console '{console}' not defined for provider '{provider.get('name')}')")
    return consoles[console]


def _github_contents_url(repo: str, path: str, branch: str) -> str:
    safe_path = quote(path)
    return f"https://api.github.com/repos/{repo}/contents/{safe_path}?ref={branch}"


def _github_request(url: str, token: Optional[str] = None) -> List[Dict]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    request = Request(url, headers=headers)
    with urlopen(request) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def generate_artwork_index(
    provider_id: str,
    console: str,
    categories: Optional[List[str]] = None,
    token: Optional[str] = None,
) -> str:
    provider = get_provider(provider_id)
    repo = provider["repository"]
    branch = provider.get("branch", "master")
    console_path = provider_console_path(provider, console)
    categories = categories or provider.get("categories", ["Named_Boxarts"])

    entries: Dict[str, Dict] = {}

    for category in categories:
        api_path = f"{console_path}/{category}"
        url = _github_contents_url(repo, api_path, branch)
        try:
            payload = _github_request(url, token=token)
        except HTTPError as exc:
            raise RuntimeError(f"GitHub API error ({exc.code}) for {api_path}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error while accessing {url}: {exc}") from exc

        if isinstance(payload, dict) and payload.get("message"):
            raise RuntimeError(payload.get("message"))

        for item in payload:
            if item.get("type") != "file":
                continue
            name = item.get("name", "")
            if not name.lower().endswith(".png"):
                continue
            normalized = name.rsplit(".", 1)[0]
            entries[normalized] = {
                "name": name,
                "category": category,
                "path": item.get("path"),
                "download_url": item.get("download_url"),
                "sha": item.get("sha"),
            }

    index_data = {
        "provider": provider_id,
        "console": console,
        "remote_path": console_path,
        "branch": branch,
        "categories": categories,
        "fetched_at": datetime.utcnow().isoformat(),
        "entries": entries,
    }

    provider_dir = os.path.join(INDEX_DIR, provider_id)
    os.makedirs(provider_dir, exist_ok=True)
    console_slug = _slugify(console)
    index_path = os.path.join(provider_dir, f"{console_slug}.json")
    with open(index_path, "w") as fh:
        json.dump(index_data, fh, indent=2)

    return index_path


def load_artwork_index(provider_id: str, console: str) -> Optional[Dict]:
    console_slug = _slugify(console)
    index_path = os.path.join(INDEX_DIR, provider_id, f"{console_slug}.json")
    if not os.path.exists(index_path):
        return None
    with open(index_path) as fh:
        return json.load(fh)


def list_artwork_consoles_with_status() -> List[Dict]:
    providers = load_artwork_providers()
    results = []
    for provider_id, provider in providers.items():
        for console in provider.get("consoles", {}).keys():
            index = load_artwork_index(provider_id, console)
            results.append({
                "provider_id": provider_id,
                "provider_name": provider.get("name"),
                "console": console,
                "remote_path": provider["consoles"][console],
                "indexed": bool(index),
                "fetched_at": index.get("fetched_at") if index else None,
            })
    return results
