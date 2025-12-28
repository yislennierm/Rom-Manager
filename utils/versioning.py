from __future__ import annotations

import json
import os
import subprocess
from typing import Optional, Tuple

import requests


GITHUB_REPO = "yislennierm/Rom-Manager"
VERSION_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "version.txt")


def _parse_version(value: str) -> Tuple[int, ...]:
    """Convert a version string like '1.2.3' into a comparable tuple."""
    parts = []
    for part in value.strip().split("."):
        try:
            parts.append(int(part))
        except ValueError:
            # Drop non-numeric suffixes safely (e.g., 1.2.3-beta -> 1.2.3)
            digits = "".join(ch for ch in part if ch.isdigit())
            parts.append(int(digits) if digits else 0)
    return tuple(parts)


def get_local_version() -> str:
    """Best-effort local version.

    Order of precedence:
    - ROMS_MANAGER_VERSION env var
    - data/version.txt if present
    - git describe --tags (if available)
    - fallback "0.0.0"
    """
    env_val = os.environ.get("ROMS_MANAGER_VERSION")
    if env_val:
        return env_val

    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            capture_output=True,
            text=True,
            check=True,
        )
        desc = result.stdout.strip()
        if desc:
            return desc.lstrip("v")
    except Exception:
        pass

    return "0.0.0"


def get_remote_version(timeout: float = 5.0) -> Optional[str]:
    """Fetch the latest release tag from GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = json.loads(resp.text)
        tag = data.get("tag_name")
        if tag:
            return tag.lstrip("v")
    except Exception:
        return None
    return None


def needs_update(local_version: Optional[str] = None) -> bool:
    """Return True if a newer version is available upstream."""
    local = local_version or get_local_version()
    remote = get_remote_version()
    if not remote:
        return False
    return _parse_version(remote) > _parse_version(local)


# -------- Branch-aware helpers (e.g., check a staging branch) -------- #
def get_local_commit() -> Optional[str]:
    """Return the current git HEAD SHA, or an env override."""
    env_sha = os.environ.get("ROMS_MANAGER_COMMIT")
    if env_sha:
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        sha = result.stdout.strip()
        return sha if sha else None
    except Exception:
        return None


def get_branch_head_sha(branch: str, timeout: float = 5.0) -> Optional[str]:
    """Fetch the latest commit SHA for a given branch from GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/branches/{branch}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = json.loads(resp.text)
        commit = data.get("commit", {})
        sha = commit.get("sha")
        return sha
    except Exception:
        return None


def needs_branch_update(branch: str, local_sha: Optional[str] = None) -> bool:
    """Return True if the remote branch head differs from local."""
    local = local_sha or get_local_commit()
    remote = get_branch_head_sha(branch)
    if not remote or not local:
        return False
    return remote != local
