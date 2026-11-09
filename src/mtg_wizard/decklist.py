"""Decklist parsing.

Accepted line formats (one card per line, case-insensitive):

    4 Lightning Bolt
    4x Lightning Bolt
    Lightning Bolt                      (quantity 1)
    1 Sol Ring (C21) 263                (MTG Arena / Moxfield export)
    1 Sol Ring [C21]
    1 Sol Ring (C21) 263 *F*            (foil)
    1 Anneau solaire                    (French names work too)

Section headers ("Deck", "Sideboard", "Commander", "Companion", "Maybeboard") and
comments (lines starting with '#' or '//') are recognised; empty lines are ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import CardRequest, name_key

SECTION_HEADERS = {
    "deck": "main", "main": "main", "mainboard": "main", "main deck": "main",
    "sideboard": "side", "side": "side", "reserve": "side",
    "commander": "commander", "commandant": "commander",
    "companion": "companion", "compagnon": "companion",
    "maybeboard": "maybe", "considering": "maybe",
    "tokens": "tokens",
}

BASIC_LANDS = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
    "plaine", "ile", "marais", "montagne", "foret",
}

_LINE = re.compile(
    r"""^\s*
    (?:(?P<qty>\d+)\s*x?\s+)?                 # quantity, optional "x"
    (?P<name>.+?)                             # card name (lazy)
    (?:\s+[\(\[](?P<set>[A-Za-z0-9]{2,6})[\)\]] # (SET) or [SET]
       (?:\s+(?P<num>[A-Za-z0-9\-★†]+))?)?     # collector number
    (?P<foil>\s+\*[FE]\*)?                    # *F* foil / *E* etched
    \s*$""",
    re.VERBOSE,
)


def parse_line(line: str) -> CardRequest | None:
    line = line.strip()
    if not line or line.startswith(("#", "//")):
        return None
    m = _LINE.match(line)
    if not m:
        return None
    name = m.group("name").strip().rstrip(",;")
    if not name:
        return None
    qty = int(m.group("qty")) if m.group("qty") else 1
    if qty <= 0:
        return None
    return CardRequest(
        name=name,
        quantity=qty,
        set_code=(m.group("set") or None) and m.group("set").lower(),
        collector_number=m.group("num"),
        foil=True if m.group("foil") else None,
    )


def parse_decklist(text: str, *, include_sections=("main", "side", "commander", "companion"),
                   skip_basic_lands: bool = False) -> list[CardRequest]:
    """Parse a decklist and merge duplicate card names (summing quantities)."""
    section = "main"
    merged: dict[str, CardRequest] = {}
    for raw in text.splitlines():
        stripped = raw.strip().rstrip(":")
        header = SECTION_HEADERS.get(stripped.lower())
        if header and not re.match(r"^\d", stripped):
            section = header
            continue
        req = parse_line(raw)
        if req is None:
            continue
        req.section = section
        if section not in include_sections:
            continue
        if skip_basic_lands and name_key(req.name) in BASIC_LANDS:
            continue
        key = name_key(req.name)
        if key in merged:
            merged[key].quantity += req.quantity
        else:
            merged[key] = req
    return list(merged.values())


def load_decklist(path: str | Path, **kwargs) -> list[CardRequest]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_decklist(text, **kwargs)
