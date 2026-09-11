"""Where each card sits on the dashboard.

A layout is columns, left to right. Each column has a width weight, a flow
(stacked, or side by side when they fit) and the cards it holds, in order.
Stored server-side, so an arrangement follows you between devices and
survives a reboot.
"""
from __future__ import annotations

import json

# Card id -> label shown while rearranging. Also the whitelist: anything
# else in a stored layout is dropped rather than rendered.
TILES: dict[str, str] = {
    "range": "Range status",
    "feed": "Live feed",
    "score": "Score",
    "scope": "Scope correction",
    "group-stats": "Group stats",
    "shots": "Shots",
    "subgroups": "Best subgroups",
}

FLOWS = ("stack", "wrap")

MAX_COLUMNS = 4
MIN_WEIGHT = 1
MAX_WEIGHT = 6

# Per-card size. Width is a share of the row, like a column's weight.
# Height is a floor in pixels, never a ceiling, so a card is never
# shorter than its contents.
MIN_TILE_WIDTH = 1
MAX_TILE_WIDTH = 6
MAX_TILE_HEIGHT = 900
TILE_HEIGHT_STEP = 80

DEFAULT_LAYOUT: dict = {
    "columns": [
        {"weight": 2, "flow": "stack", "tiles": ["range", "feed", "score", "scope"]},
        {"weight": 3, "flow": "wrap", "tiles": ["group-stats", "shots", "subgroups"]},
    ],
    # Cards put away while arranging. Listed rather than dropped, so they
    # can be brought back and so the rule below can tell a card hidden on
    # purpose from one written before that card existed.
    "hidden": [],
    # tile id -> {"w": share, "h": minimum height in px}. Only cards that
    # differ from the default are listed.
    "sizes": {},
}

# Where a card goes when a stored layout doesn't mention it -- a layout
# saved before a card existed must not make that card disappear.
_HOME_COLUMN = {
    tile: index
    for index, column in enumerate(DEFAULT_LAYOUT["columns"])
    for tile in column["tiles"]
}


def default_layout() -> dict:
    """A fresh copy of the default, safe for the caller to modify."""
    return json.loads(json.dumps(DEFAULT_LAYOUT))


def _clean_int(raw, low, high, fallback):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, value))


def _clean_sizes(raw) -> dict:
    """Per-card sizes, keeping only what differs from the default."""
    sizes: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return sizes
    for tile, value in raw.items():
        if tile not in TILES or not isinstance(value, dict):
            continue
        width = _clean_int(value.get("w"), MIN_TILE_WIDTH, MAX_TILE_WIDTH, 1)
        height = _clean_int(value.get("h"), 0, MAX_TILE_HEIGHT, 0)
        if width != 1 or height:
            sizes[tile] = {"w": width, "h": height}
    return sizes


def _clean_weight(raw) -> int:
    try:
        weight = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(MIN_WEIGHT, min(MAX_WEIGHT, weight))


def clean_layout(raw) -> dict:
    """Whatever was stored, turned into a layout that renders.

    Self-healing rather than strict: unknown cards are dropped, duplicates
    collapse to their first position, and any card the layout never
    mentions goes back where it started. A stored layout is only ever as
    new as the version that wrote it.
    """
    if not isinstance(raw, dict):
        return default_layout()

    hidden = [t for t in (raw.get("hidden") or []) if t in TILES]
    sizes = _clean_sizes(raw.get("sizes"))

    columns = []
    seen: set[str] = set(hidden)
    for entry in (raw.get("columns") or [])[:MAX_COLUMNS]:
        if not isinstance(entry, dict):
            continue
        tiles = []
        for tile in entry.get("tiles") or []:
            if tile in TILES and tile not in seen:
                seen.add(tile)
                tiles.append(tile)
        flow = entry.get("flow")
        columns.append({
            "weight": _clean_weight(entry.get("weight")),
            "flow": flow if flow in FLOWS else "stack",
            "tiles": tiles,
        })

    for tile in TILES:
        if tile in seen:
            continue
        if not columns:
            break
        index = min(_HOME_COLUMN.get(tile, len(columns) - 1), len(columns) - 1)
        columns[index]["tiles"].append(tile)

    # An empty column is a gap you can't drop into once editing is off, so
    # only keep one if it is the last thing standing.
    columns = [c for c in columns if c["tiles"]]
    if not columns:
        if hidden:
            # Every card hidden is a legitimate arrangement, so keep one empty
            # column to drop them back into.
            return {"columns": [{"weight": 2, "flow": "stack", "tiles": []}],
                    "hidden": hidden, "sizes": sizes}
        return default_layout()
    return {"columns": columns, "hidden": hidden, "sizes": sizes}


def loads(raw: str | None) -> dict:
    """Parse a stored layout, falling back to the default on anything bad."""
    if not raw:
        return default_layout()
    try:
        return clean_layout(json.loads(raw))
    except (ValueError, TypeError):
        return default_layout()


def dumps(layout: dict) -> str:
    return json.dumps(clean_layout(layout))
