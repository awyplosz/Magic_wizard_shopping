"""Core data structures shared by the parser, providers, optimizer and report."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import IntEnum


class Condition(IntEnum):
    """Card condition on a single scale (Cardmarket grading, higher is better)."""

    PO = 1  # poor / damaged
    PL = 2  # played / heavily played
    LP = 3  # light played (Cardmarket "LP")
    GD = 4  # good (Cardmarket "GD", TCGplayer "Moderately Played", CK "VG")
    EX = 5  # excellent (TCGplayer "Lightly Played", CK "EX")
    NM = 6  # near mint
    MT = 7  # mint

    @classmethod
    def parse(cls, value: str | None, default: "Condition | None" = None) -> "Condition | None":
        """Best-effort mapping of the condition labels used by the different shops."""
        if value is None:
            return default
        v = strip_accents(str(value)).strip().lower()
        if not v:
            return default
        table = [
            (("mint/nmint", "mint/near mint", "near mint", "nm", "neuf", "new", "comme neuf"), cls.NM),
            (("mint", "mt"), cls.MT),
            (("excellent", "ex", "lightly played", "light_played_tcg"), cls.EX),
            (("good", "gd", "bon etat", "very good", "vg", "moderately played", "tres bon"), cls.GD),
            (("light played", "light_played", "lp", "legerement joue"), cls.LP),
            (("played", "pl", "heavily played", "hp", "joue", "g"), cls.PL),
            (("poor", "po", "damaged", "abime", "mauvais"), cls.PO),
        ]
        for labels, cond in table:
            if v in labels:
                return cond
        # substring fallbacks ("Near Mint Foil", "Etat : Excellent", ...)
        for labels, cond in table:
            if any(len(lbl) > 3 and lbl in v for lbl in labels):
                return cond
        return default


LANG_ALIASES = {
    "en": "en", "english": "en", "anglais": "en", "eng": "en",
    "fr": "fr", "french": "fr", "francais": "fr", "fra": "fr",
    "de": "de", "german": "de", "allemand": "de",
    "es": "es", "spanish": "es", "espagnol": "es",
    "it": "it", "italian": "it", "italien": "it",
    "pt": "pt", "portuguese": "pt", "portugais": "pt",
    "ja": "ja", "jp": "ja", "japanese": "ja", "japonais": "ja",
    "ru": "ru", "russian": "ru", "russe": "ru",
    "ko": "ko", "korean": "ko", "coreen": "ko",
    "zhs": "zhs", "s-chinese": "zhs", "chinese": "zhs", "chinois": "zhs",
    "zht": "zht", "t-chinese": "zht",
}


def normalize_lang(value: str | None) -> str | None:
    if not value:
        return None
    v = strip_accents(str(value)).strip().lower()
    return LANG_ALIASES.get(v, v)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def name_key(name: str) -> str:
    """Normalized key for comparing card names across sites (case/accents/punctuation)."""
    n = strip_accents(name).lower()
    n = n.replace("’", "'").replace("`", "'")
    n = re.sub(r"\s*/+\s*", " // ", n)  # "Fire/Ice" -> "fire // ice"
    n = re.sub(r"[^a-z0-9/' ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


@dataclass
class CardRequest:
    """One line of the decklist (after merging duplicates)."""

    name: str
    quantity: int
    set_code: str | None = None
    collector_number: str | None = None
    foil: bool | None = None
    section: str = "main"


@dataclass
class Printing:
    scryfall_id: str
    set_code: str
    set_name: str
    collector_number: str
    cardmarket_id: int | None = None
    tcgplayer_id: int | None = None
    price_eur: float | None = None
    price_usd: float | None = None
    foil_available: bool = True
    nonfoil_available: bool = True


@dataclass
class ResolvedCard:
    request: CardRequest
    name: str  # canonical English (oracle) name
    oracle_id: str | None = None
    names: dict[str, str] = field(default_factory=dict)  # lang -> printed name (e.g. {"fr": "Anneau solaire"})
    printings: list[Printing] = field(default_factory=list)

    @property
    def quantity(self) -> int:
        return self.request.quantity

    @property
    def key(self) -> str:
        return name_key(self.name)

    def all_name_keys(self) -> set[str]:
        keys = {self.key, name_key(self.request.name)}
        keys.update(name_key(n) for n in self.names.values())
        # double-faced cards are often listed by their front face only
        if " // " in self.name:
            keys.add(name_key(self.name.split(" // ")[0]))
        return keys

    def printing_by_scryfall(self) -> dict[str, Printing]:
        return {p.scryfall_id: p for p in self.printings}


@dataclass
class Offer:
    """A quantity of one card available from one seller at one unit price."""

    card: str  # canonical English card name (ResolvedCard.name)
    provider: str  # e.g. "cardmarket", "parkage"
    seller_id: str  # unique within provider
    seller_name: str
    price_eur: float  # unit price, already converted to EUR
    quantity: int
    seller_country: str | None = None  # ISO-2
    condition: Condition | None = None
    language: str | None = None
    foil: bool = False
    set_code: str | None = None
    set_name: str | None = None
    url: str | None = None
    estimate: bool = False  # True when the price is an estimate (not a real listing)
    original_price: float | None = None
    original_currency: str = "EUR"

    @property
    def seller_key(self) -> str:
        return f"{self.provider}:{self.seller_id}"

    def describe(self) -> str:
        parts = [self.set_code.upper() if self.set_code else None,
                 self.language.upper() if self.language else None,
                 self.condition.name if self.condition else None,
                 "FOIL" if self.foil else None]
        return " ".join(p for p in parts if p)
