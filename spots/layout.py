"""Where each card sits on the dashboard.

The arrangement is stored server-side rather than in the browser, so it
follows you from the phone at the bench to the laptop afterwards and
survives a reboot -- the same reason the equipment selection lives there.

A layout is columns, left to right. Each column has a width weight, a flow
(stacked, or side by side when they fit) and the cards it holds, in order.
The default reproduces the original hand-written layout exactly, so an
install that has never been rearranged looks untouched.
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

DEFAULT_LAYOUT: dict = {
    "columns": [
        {"weight": 2, "flow": "stack", "tiles": ["range", "feed", "score", "scope"]},
        {"weight": 3, "flow": "wrap", "tiles": ["group-stats", "shots", "subgroups"]},
    ],
    # Cards put away while arranging. Kept as a list rather than dropped, so
    # a card can be brought back -- and so the "any tile the layout doesn't
    # mention goes back where it started" rule below can tell "hidden on
    # purpose" apart from "written before this card existed".
    "hidden": [],
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


def _clean_weight(raw) -> int:
    try:
        weight = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(MIN_WEIGHT, min(MAX_WEIGHT, weight))


def clean_layout(raw) -> dict:
    """Whatever was stored, turned into a layout that renders.

    Self-healing rather than strict: unknown cards are dropped, duplicates
    collapse to their first position, and any card the layout never mentions
    is put back where it started. A stored layout is only ever as new as the
    version that wrote it, and a card going missing from the dashboard with
    no way to get it back is a far worse failure than a moved one.
    """
    if not isinstance(raw, dict):
        return default_layout()

    hidden = [t for t in (raw.get("hidden") or []) if t in TILES]

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
            # Every card put away is a legitimate arrangement, so keep one
            # empty column to drop them back into. Falling through to the
            # default here would quietly un-hide the lot.
            return {"columns": [{"weight": 2, "flow": "stack", "tiles": []}],
                    "hidden": hidden}
        return default_layout()
    return {"columns": columns, "hidden": hidden}


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
