"""The shooting ranges built into the app.

One JSON file per range in data/ranges, transcribed from that range's own
published rules -- there is no way to add a range from the app, because a
range's rules are a safety document and having them typed in by whoever is
holding the phone is worse than not having them at all. Each file records
where its contents came from and when, so a copy that has gone stale can
be recognised as stale rather than trusted.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RANGES_DIR = Path(__file__).parent / "data" / "ranges"

# Ids come in from the URL and end up in a filename, so nothing but this.
_ID = re.compile(r"^[a-z0-9-]+$")

_cache: dict[str, dict] | None = None


def _load_all() -> dict[str, dict]:
    ranges: dict[str, dict] = {}
    if not RANGES_DIR.is_dir():
        return ranges
    for path in sorted(RANGES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # One unreadable file shouldn't take the whole page down.
            logger.exception("Could not read range file %s", path)
            continue
        range_id = data.get("id") or path.stem
        if not _ID.match(str(range_id)):
            logger.warning("Ignoring range file %s: bad id %r", path, range_id)
            continue
        data["id"] = range_id
        data.setdefault("name", range_id)
        data.setdefault("short_name", data["name"])
        data.setdefault("sections", [])
        ranges[range_id] = data
    return ranges


def all_ranges() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = _load_all()
    return _cache


def list_ranges() -> list[dict]:
    """Every range, without the rules -- enough to build the picker."""
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "short_name": item["short_name"],
            "operator": item.get("operator", ""),
            "address": item.get("address", ""),
            "section_count": len(item.get("sections", [])),
        }
        for item in sorted(all_ranges().values(), key=lambda r: r["name"])
    ]


def get_range(range_id: str | None) -> dict | None:
    if not range_id or not _ID.match(range_id):
        return None
    return all_ranges().get(range_id)


def default_range_id() -> str | None:
    listed = list_ranges()
    return listed[0]["id"] if listed else None


def rule_count(item: dict) -> int:
    return sum(
        1
        for section in item.get("sections", [])
        for block in section.get("blocks", [])
        if block.get("kind") == "rule"
    )
