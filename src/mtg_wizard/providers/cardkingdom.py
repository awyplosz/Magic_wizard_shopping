"""Card Kingdom (USA) - uses the public daily price list (api.cardkingdom.com/api/v2/pricelist).

One JSON file (~70 MB, cached 12 h) gives price and stock per printing and condition,
matched to cards through the Scryfall id. Prices are USD; importing to France adds VAT
and a carrier handling fee (see config).
"""

from __future__ import annotations

import json
from collections import defaultdict

from ..models import Condition, Offer, ResolvedCard
from ..shipping import ShippingRule
from .base import Provider, Seller

PRICELIST_URL = "https://api.cardkingdom.com/api/v2/pricelist"
CK_CONDITIONS = [("nm", Condition.NM), ("ex", Condition.EX), ("vg", Condition.GD), ("g", Condition.PL)]


class CardKingdomProvider(Provider):
    name = "cardkingdom"
    label = "Card Kingdom"

    def prepare(self, cards):
        path = self.http.download_file(PRICELIST_URL, self.http.cache_dir / "cardkingdom" / "pricelist.json",
                                       ttl_hours=12)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.base_url = data.get("meta", {}).get("base_url", "https://www.cardkingdom.com/")
        wanted = {p.scryfall_id for c in cards for p in c.printings}
        self.by_scryfall: dict[str, list[dict]] = defaultdict(list)
        for row in data.get("data", []):
            sid = row.get("scryfall_id")
            if sid in wanted:
                self.by_scryfall[sid].append(row)
        self.seller = Seller(key=f"{self.name}:ck", provider=self.name, name="Card Kingdom",
                             country="US", shipping=ShippingRule.from_dict(self.cfg.get("shipping")),
                             url="https://www.cardkingdom.com")
        self.ctx.add_seller(self.seller)

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        offers = []
        for p in self.candidate_printings(card):
            for row in self.by_scryfall.get(p.scryfall_id, []):
                foil = str(row.get("is_foil")).lower() == "true"
                cv = row.get("condition_values") or {}
                for code, cond in CK_CONDITIONS:
                    qty = int(cv.get(f"{code}_qty") or 0)
                    price = float(cv.get(f"{code}_price") or 0)
                    if qty <= 0 or price <= 0:
                        continue
                    offers.append(Offer(
                        card=card.name, provider=self.name, seller_id="ck", seller_name="Card Kingdom",
                        seller_country="US", price_eur=round(price * self.ctx.usd_to_eur, 2), quantity=qty,
                        condition=cond, language="en", foil=foil, set_code=p.set_code, set_name=p.set_name,
                        url=self.base_url + row.get("url", ""), original_price=price, original_currency="USD"))
        return offers
