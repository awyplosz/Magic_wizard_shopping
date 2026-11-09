"""Troll2Jeux (Paris) - PrestaShop store.

The search page lists products titled "<French name> (<English name>)"; each product page
embeds a `data-product` JSON with price, stock and the edition. One product = one edition.
The site does not state language/condition per product: language defaults to the
`default_language` config value (French) and condition is left unknown.
"""

from __future__ import annotations

import html as htmllib
import json
import re

from bs4 import BeautifulSoup

from ..models import Offer, ResolvedCard, name_key
from ..shipping import ShippingRule
from .base import Provider, Seller

BASE = "https://troll2jeux.com"
SEARCH_URL = BASE + "/recherche"


def parse_search(html: str) -> list[tuple[str, str]]:
    """-> [(title, product url)]"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for art in soup.select("article.js-product-miniature"):
        a = art.select_one("h3 a") or art.select_one("a.product_img_link")
        if a and a.get("href"):
            out.append((a.get("title") or a.get_text(strip=True), a["href"]))
    return out


def title_matches(title: str, card: ResolvedCard) -> bool:
    keys = card.all_name_keys()
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", title)
    if m:
        return name_key(m.group(1)) in keys or name_key(m.group(2)) in keys
    return name_key(title) in keys


def parse_product(html: str) -> dict | None:
    m = re.search(r'data-product="([^"]+)"', html)
    if not m:
        return None
    d = json.loads(htmllib.unescape(m.group(1)))
    edition = next((f.get("value") for f in d.get("features") or [] if "edition" in f.get("name", "").lower()),
                   None)
    ref = d.get("reference") or ""
    set_code = None
    rm = re.match(r"MTG-([A-Z0-9]{2,6})-", ref)
    if rm:
        set_code = rm.group(1).lower()
    name = (d.get("name") or "")
    return {
        "id": d.get("id_product"), "name": name, "price": float(d.get("price_amount") or 0),
        "qty": int(d.get("quantity") or 0), "set_code": set_code,
        "set_name": edition.replace("\xa0", " ").strip() if edition else None,
        "foil": "foil" in name.lower(),
    }


class Troll2JeuxProvider(Provider):
    name = "troll2jeux"
    label = "Troll2Jeux (Paris)"

    def prepare(self, cards):
        self.seller = Seller(key=f"{self.name}:shop", provider=self.name, name="Troll2Jeux", country="FR",
                             pickup=True, url=BASE,
                             shipping=ShippingRule(visit_cost_eur=float(self.cfg.get("visit_cost_eur", 0))))
        self.ctx.add_seller(self.seller)
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"}

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        html = self.http.get_text_cached("troll2jeux", SEARCH_URL, ttl_hours=3, headers=self.headers,
                                         params={"controller": "search", "s": card.name, "resultsPerPage": 48})
        links = [(t, u) for t, u in parse_search(html) if title_matches(t, card)]
        offers = []
        for _title, url in links[:15]:
            page = self.http.get_text_cached("troll2jeux", url, ttl_hours=3, headers=self.headers)
            p = parse_product(page)
            if not p or p["qty"] <= 0 or p["price"] <= 0:
                continue
            offers.append(Offer(card=card.name, provider=self.name, seller_id="shop", seller_name="Troll2Jeux",
                                seller_country="FR", price_eur=p["price"], quantity=p["qty"],
                                language=self.cfg.get("default_language"), foil=p["foil"],
                                set_code=p["set_code"], set_name=p["set_name"], url=url))
        return offers
