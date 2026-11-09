"""Manual offers from CSV files - for any shop without a scraper, or prices you noted in store. Columns (header row required, ';' or ',' separated):

    shop,card,price_eur,quantity[,language,condition,foil,set,shipping_eur,pickup]

`card` may be the English or French name. `shipping_eur` is a flat parcel cost for that shop
(default 0, i.e. pick-up in Paris).
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import Condition, Offer, ResolvedCard, name_key, normalize_lang
from ..shipping import ShippingRule
from .base import Provider, ProviderUnavailable, Seller


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "oui", "y", "x", "foil")


class CsvProvider(Provider):
    name = "csv"
    label = "Manual CSV offers"

    def prepare(self, cards):
        files = [Path(f) for f in self.cfg.get("files") or []]
        if not files:
            raise ProviderUnavailable("no CSV files configured")
        index = {k: c for c in cards for k in c.all_name_keys()}
        self.rows: dict[str, list[dict]] = {}
        for f in files:
            if not f.exists():
                self.notes.append(f"CSV file not found: {f}")
                continue
            text = f.read_text(encoding="utf-8-sig")
            dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
            for row in csv.DictReader(text.splitlines(), dialect=dialect):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                card = index.get(name_key(row.get("card", "")))
                if card is None:
                    continue
                shop = row.get("shop") or f.stem
                ship = float((row.get("shipping_eur") or "0").replace(",", "."))
                self.ctx.add_seller(Seller(
                    key=f"{self.name}:{shop}", provider=self.name, name=shop, country="FR",
                    pickup=ship == 0,
                    shipping=ShippingRule(tiers=[(100000, ship)],
                                          visit_cost_eur=float(self.cfg.get("visit_cost_eur", 0)) if ship == 0 else 0)))
                self.rows.setdefault(card.name, []).append({**row, "shop": shop})

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        out = []
        for r in self.rows.get(card.name, []):
            out.append(Offer(card=card.name, provider=self.name, seller_id=r["shop"], seller_name=r["shop"],
                             seller_country="FR", price_eur=float(r.get("price_eur", "0").replace(",", ".")),
                             quantity=int(r.get("quantity") or 1), language=normalize_lang(r.get("language")),
                             condition=Condition.parse(r.get("condition")), foil=_truthy(r.get("foil")),
                             set_code=(r.get("set") or "").lower() or None))
        return out
