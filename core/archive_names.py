"""Dependency-free catalog title/region matching for archive ROM selection."""
import re
from pathlib import PurePosixPath
from utils.catalog import _release_qualifiers

REGIONS = {'u': 'usa', 'us': 'usa', 'usa': 'usa', 'j': 'japan', 'jp': 'japan', 'japan': 'japan',
           'e': 'europe', 'eu': 'europe', 'europe': 'europe', 'k': 'korea', 'kr': 'korea',
           'korea': 'korea', 'w': 'world', 'world': 'world', 'asia': 'asia'}


def parts(value):
    name = PurePosixPath(value).name.lower()
    if name.endswith(('.sfc', '.smc', '.bin', '.nes', '.md', '.gen')):
        name = name.rsplit('.', 1)[0]
    groups = re.findall(r'\(([^)]*)\)', name)
    region = next((REGIONS[token] for group in groups for token in re.split(r'[,/ ]+', group) if token in REGIONS), None)
    version = next((re.sub('[^a-z0-9]', '', group) for group in groups if re.match(r'^(v(?:ersion)?\s*\d|rev(?:ision)?\s*\d)', group)), None)
    title = re.split(r'\s*[\[(]', name, maxsplit=1)[0].strip()
    match = re.match(r'^(.*),\s*(the|an|a)$', title)
    if match:
        title = match.group(2) + ' ' + match.group(1)
    return re.sub('[^a-z0-9]', '', title), region, version


def select_archive_rom_name(title, names):
    requested, region, version = parts(title)
    candidates = []
    for name in names:
        if _release_qualifiers(title) != _release_qualifiers(name):
            continue
        flags = re.findall(r'\[([^\]]*)\]', name)
        if any(flag.strip() != '!' for flag in flags):
            continue
        candidate, candidate_region, candidate_version = parts(name)
        if candidate != requested:
            continue
        if region and candidate_region and region != candidate_region and 'world' not in (region, candidate_region):
            continue
        if version and candidate_version != version:
            continue
        candidates.append(name)
    if not version:
        unversioned = [name for name in candidates if not parts(name)[2]]
        if unversioned:
            candidates = unversioned
    verified = [name for name in candidates if '[!]' in name]
    if len(verified) == 1:
        return verified[0]
    return candidates[0] if len(candidates) == 1 else None
