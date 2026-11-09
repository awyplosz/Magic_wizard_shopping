"""Parkage (Paris) - JSON search API used by parkage.com (back.parkage.com).

Each product is one card + edition + language + condition, with stock and price.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import Condition, Offer, ResolvedCard, name_key, normalize_lang
from ..shipping import ShippingRule
from .base import Provider, Seller

SEARCH_URL = "https://back.parkage.com/api/parkage/search/get"
SINGLES_TYPE_ID = 9  # "Cartes à l'unité Magic: The Gathering"
STATES = {"new": Condition.NM, "near_mint": Condition.NM, "excellent": Condition.EX,
          "good": Condition.GD, "light_played": Condition.LP, "played": Condition.PL,
          "poor": Condition.PO, "damaged": Condition.PO}


def _taxonomy(item: dict, name: str) -> list[dict]:
    for t in item.get("taxonomies") or []:
        if t.get("name") == name:
            return t.get("values") or []
    return []


def parse_items(items: list[dict], card: ResolvedCard) -> list[dict]:
    keys = card.all_name_keys()
    out = []
    for it in items:
        if not (name_key(it.get("name_en") or "") in keys or name_key(it.get("name_fr") or "") in keys):
            continue
        types = {v.get("id") for v in _taxonomy(it, "product_type_id")}
        if types and SINGLES_TYPE_ID not in types:
            continue
        if not it.get("canSelling", True) or it.get("deleted"):
            continue
        qty = int(it.get("maxQuantity") or it.get("displayStock") or it.get("stock") or 0)
        price = float(it.get("price") or 0)
        disc = float(it.get("price_discount") or 0)
        if 0 < disc < price:
            price = disc
        sets = _taxonomy(it, "set_id")
        out.append({
            "id": it.get("id"), "qty": qty, "price": price,
            "condition": STATES.get(str(it.get("state", "")).lower()) or Condition.parse(it.get("state_en")),
            "language": normalize_lang(it.get("lang")), "foil": bool(it.get("is_foil")),
            "set_code": (sets[0].get("abreviation") or "").lower() if sets else None,
            "set_name": sets[0].get("name_en") if sets else None,
        })
    return out


class ParkageProvider(Provider):
    name = "parkage"
    label = "Parkage (Paris)"

    def prepare(self, cards):
        self.seller = Seller(key=f"{self.name}:shop", provider=self.name, name="Parkage", country="FR",
                             pickup=True, url="https://www.parkage.com",
                             shipping=ShippingRule(visit_cost_eur=float(self.cfg.get("visit_cost_eur", 0))))
        self.ctx.add_seller(self.seller)

    def _search(self, text: str) -> list[dict]:
        params = {"search": "", "complete": "true", "count": "true", "limit": 100, "offset": 0,
                  "text": text, "with_quantity": "true", "order": "relevance", "direction": "DESC"}
        data = self.http.get_json_cached("parkage", SEARCH_URL, ttl_hours=3, params=params,
                                         headers={"Accept": "application/json"})
        return (data.get("data") or {}).get("list") or []

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        items = parse_items(self._search(card.name), card)
        if not items and card.names.get("fr"):
            items = parse_items(self._search(card.names["fr"]), card)
        return [Offer(card=card.name, provider=self.name, seller_id="shop", seller_name="Parkage",
                      seller_country="FR", price_eur=i["price"], quantity=i["qty"], condition=i["condition"],
                      language=i["language"], foil=i["foil"], set_code=i["set_code"], set_name=i["set_name"],
                      url=f"https://www.parkage.com/fr/recherche?search={quote_plus(card.name)}")
                for i in items if i["qty"] > 0]
