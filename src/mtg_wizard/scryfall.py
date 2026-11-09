"""Resolve decklist names (English or French) to canonical cards + all their printings via Scryfall."""

from __future__ import annotations

import logging

import requests

from .http import HttpClient
from .models import CardRequest, Printing, ResolvedCard, name_key

log = logging.getLogger(__name__)

API = "https://api.scryfall.com"
TTL = 24 * 7  # hours; card data barely changes, prices are not taken from here


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ScryfallResolver:
    def __init__(self, http: HttpClient):
        self.http = http
        self.headers = {"Accept": "application/json"}
        self.http.host_delays.setdefault("api.scryfall.com", 0.12)  # Scryfall asks for <= 10 req/s

    # ------------------------------------------------------------- name lookup
    def _collection(self, names: list[str]) -> tuple[dict[str, dict], list[str]]:
        found: dict[str, dict] = {}
        missing: list[str] = []
        for i in range(0, len(names), 75):
            chunk = names[i:i + 75]
            data = self.http.get_json_cached(
                "scryfall", f"{API}/cards/collection", TTL, method="POST",
                json={"identifiers": [{"name": n} for n in chunk]}, headers=self.headers)
            by_key = {}
            for card in data.get("data", []):
                by_key[name_key(card["name"])] = card
                for face in card.get("card_faces", []) or []:
                    by_key.setdefault(name_key(face["name"]), card)
            for n in chunk:
                card = by_key.get(name_key(n))
                if card:
                    found[n] = card
                else:
                    missing.append(n)
        return found, missing

    def _search_foreign(self, name: str) -> tuple[dict | None, str | None, str | None]:
        """Find a card by its printed name in any language. Returns (card, lang, printed_name)."""
        for q in (f'!"{name}" lang:fr', f'!"{name}" lang:any', f'"{name}" lang:any'):
            try:
                data = self.http.get_json_cached("scryfall", f"{API}/cards/search", TTL,
                                                 params={"q": q, "unique": "cards"}, headers=self.headers)
            except requests.HTTPError:
                continue  # 404 = no match
            for card in data.get("data", []):
                printed = card.get("printed_name") or card.get("name")
                if name_key(printed) == name_key(name) or len(data.get("data", [])) == 1:
                    return card, card.get("lang"), printed
        return None, None, None

    def _fuzzy(self, name: str) -> dict | None:
        try:
            return self.http.get_json_cached("scryfall", f"{API}/cards/named", TTL,
                                             params={"fuzzy": name}, headers=self.headers)
        except requests.HTTPError:
            return None

    # --------------------------------------------------------------- printings
    def printings(self, oracle_name: str) -> list[Printing]:
        out: list[Printing] = []
        url = f"{API}/cards/search"
        params = {"q": f'!"{oracle_name}" game:paper', "unique": "prints", "order": "released"}
        while url:
            try:
                data = self.http.get_json_cached("scryfall", url, TTL, params=params, headers=self.headers)
            except requests.HTTPError as exc:
                log.warning("Scryfall printings lookup failed for %s: %s", oracle_name, exc)
                break
            for c in data.get("data", []):
                finishes = c.get("finishes") or []
                prices = c.get("prices") or {}
                out.append(Printing(
                    scryfall_id=c["id"],
                    set_code=c.get("set", ""),
                    set_name=c.get("set_name", ""),
                    collector_number=c.get("collector_number", ""),
                    cardmarket_id=c.get("cardmarket_id"),
                    tcgplayer_id=c.get("tcgplayer_id"),
                    price_eur=_float(prices.get("eur")) or _float(prices.get("eur_foil")),
                    price_usd=_float(prices.get("usd")) or _float(prices.get("usd_foil")),
                    foil_available="foil" in finishes or "etched" in finishes,
                    nonfoil_available="nonfoil" in finishes or not finishes,
                ))
            url = data.get("next_page") if data.get("has_more") else None
            params = None  # next_page already contains the query
        return out

    def french_name(self, oracle_name: str) -> str | None:
        try:
            data = self.http.get_json_cached("scryfall", f"{API}/cards/search", TTL,
                                             params={"q": f'!"{oracle_name}" lang:fr', "unique": "cards"},
                                             headers=self.headers)
        except requests.HTTPError:
            return None
        for c in data.get("data", []):
            if c.get("printed_name"):
                return c["printed_name"]
            for f in c.get("card_faces", []) or []:
                if f.get("printed_name"):
                    return f["printed_name"]
        return None

    # -------------------------------------------------------------------- main
    def resolve(self, requests_: list[CardRequest], *, with_french_names: bool = True,
                progress=None) -> tuple[list[ResolvedCard], list[CardRequest]]:
        """Returns (resolved cards, requests that could not be identified)."""
        names = [r.name for r in requests_]
        found, missing = self._collection(names)
        printed: dict[str, tuple[str, str]] = {}
        for n in missing:
            card, lang, pname = self._search_foreign(n)
            if card is None:
                card = self._fuzzy(n)
            if card is not None:
                found[n] = card
                if lang and lang != "en" and pname:
                    printed[n] = (lang, pname)

        resolved: dict[str, ResolvedCard] = {}
        unknown: list[CardRequest] = []
        for i, req in enumerate(requests_):
            card = found.get(req.name)
            if card is None:
                unknown.append(req)
                continue
            oracle_name = card["name"]
            key = name_key(oracle_name)
            if key in resolved:  # same card listed twice (e.g. EN + FR name)
                resolved[key].request.quantity += req.quantity
                continue
            rc = ResolvedCard(request=req, name=oracle_name, oracle_id=card.get("oracle_id"))
            if req.name in printed:
                lang, pname = printed[req.name]
                rc.names[lang] = pname
            if progress:
                progress(i + 1, len(requests_), oracle_name)
            rc.printings = self.printings(oracle_name)
            if with_french_names and "fr" not in rc.names:
                fr = self.french_name(oracle_name)
                if fr:
                    rc.names["fr"] = fr
            resolved[key] = rc
        return list(resolved.values()), unknown
