"""Cardmarket (Europe).

cardmarket.com blocks automated access and its API accepts no new applications, so this tool
does NOT scrape the live site. Two data sources are used (config `providers.cardmarket.modes`):

* ``saved_pages`` - product pages you saved yourself from your browser (Ctrl+S, "web page,
                    HTML only") into `cardmarket_pages/`. Their offer tables are parsed
                    (real sellers, countries, conditions, languages, prices, stock).
* ``priceguide``  - the public daily price guide published by Cardmarket
                    (downloads.s3.cardmarket.com). Gives a price ESTIMATE per printing
                    (trend/low...), no seller detail. Used only for cards that have no saved page.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..models import Condition, Offer, ResolvedCard, name_key, normalize_lang, strip_accents
from ..shipping import ShippingRule
from .base import Provider, ProviderUnavailable, Seller

log = logging.getLogger(__name__)

PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"
COUNTRY_NAMES = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czech republic": "CZ", "czechia": "CZ", "denmark": "DK", "estonia": "EE", "finland": "FI",
    "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU", "ireland": "IE", "italy": "IT",
    "latvia": "LV", "lithuania": "LT", "luxembourg": "LU", "malta": "MT", "netherlands": "NL",
    "poland": "PL", "portugal": "PT", "romania": "RO", "slovakia": "SK", "slovenia": "SI",
    "spain": "ES", "sweden": "SE", "united kingdom": "GB", "switzerland": "CH", "norway": "NO",
    "iceland": "IS", "liechtenstein": "LI", "japan": "JP", "singapore": "SG", "canada": "CA",
    # French UI
    "autriche": "AT", "belgique": "BE", "allemagne": "DE", "espagne": "ES", "italie": "IT",
    "pays-bas": "NL", "pologne": "PL", "royaume-uni": "GB", "suisse": "CH", "suede": "SE",
    "grece": "GR", "irlande": "IE", "danemark": "DK", "finlande": "FI", "hongrie": "HU",
    "roumanie": "RO", "republique tcheque": "CZ", "slovaquie": "SK", "slovenie": "SI",
    "croatie": "HR", "lettonie": "LV", "lituanie": "LT", "estonie": "EE", "bulgarie": "BG",
    "chypre": "CY", "malte": "MT", "japon": "JP", "norvege": "NO", "islande": "IS",
}


def _country_from_label(label: str | None) -> str | None:
    if not label:
        return None
    v = strip_accents(label).lower()
    v = re.sub(r"^.*?(location|localisation|lieu)[^:]*:\s*", "", v).strip()
    return COUNTRY_NAMES.get(v)


def _price(text: str) -> float | None:
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{1,2})?)\s*€", text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    return float(raw)


def parse_product_page(html: str) -> tuple[str, list[dict]]:
    """Parse a saved cardmarket.com product page -> (product title, list of article dicts)."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = " ".join(h1.stripped_strings) if h1 else ""
    attrs = ("aria-label", "data-bs-original-title", "data-original-title", "title")
    articles = []
    for row in soup.select("div.article-row"):
        seller_el = row.select_one(".seller-name a") or row.select_one("a[href*='/Users/']")
        if not seller_el:
            continue
        seller = seller_el.get_text(strip=True)
        country = None
        for el in row.select(", ".join(f".seller-name [{a}]" for a in attrs)):
            for attr in attrs:
                country = country or _country_from_label(el.get(attr))
        cond_el = row.select_one(".article-condition")
        condition = Condition.parse(cond_el.get_text(strip=True) if cond_el else None)
        language, foil = None, False
        for el in row.select(", ".join(f".product-attributes [{a}]" for a in attrs)):
            for attr in attrs:
                val = el.get(attr)
                if not val:
                    continue
                if "foil" in val.lower():
                    foil = True
                lang = normalize_lang(val)
                if lang and len(lang) <= 3 and language is None:
                    language = lang
        price_el = row.select_one(".price-container") or row.select_one(".col-offer")
        price = _price(price_el.get_text(" ", strip=True)) if price_el else None
        qty_el = row.select_one(".amount-container .item-count") or row.select_one(".item-count")
        try:
            qty = int(qty_el.get_text(strip=True)) if qty_el else 1
        except ValueError:
            qty = 1
        if price:
            articles.append({"seller": seller, "country": country, "condition": condition,
                             "language": language, "foil": foil, "price": price, "quantity": qty})
    return title, articles


class CardmarketProvider(Provider):
    name = "cardmarket"
    label = "Cardmarket"

    def prepare(self, cards):
        modes = self.cfg.get("modes", ["priceguide", "saved_pages"])
        self.allowed_countries = set(self.cfg.get("seller_countries") or [])
        self.by_country = {k: ShippingRule.from_dict(v) for k, v in
                           (self.cfg.get("shipping_by_country") or {}).items()}
        self.saved: dict[str, list[dict]] = {}
        if "saved_pages" in modes:
            self._load_saved_pages(cards)
        self.guide = None
        if "priceguide" in modes and self.ctx.cfg.get("solver", {}).get("include_estimates", True):
            try:
                path = self.http.download_file(PRICE_GUIDE_URL,
                                               self.http.cache_dir / "cardmarket" / "price_guide.json", 20)
                with open(path, encoding="utf-8") as fh:
                    self.guide = {g["idProduct"]: g for g in json.load(fh).get("priceGuides", [])}
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"Cardmarket price guide unavailable: {exc}")
        if not (self.saved or self.guide):
            raise ProviderUnavailable("no saved Cardmarket pages and no price guide (see README: Cardmarket)")

    # ---------------------------------------------------------------- sellers
    def _seller(self, username: str, country: str | None) -> Seller:
        rule = self.by_country.get(country or "") or self.by_country.get("default") or ShippingRule()
        s = Seller(key=f"{self.name}:{username}", provider=self.name, name=username, country=country,
                   shipping=rule, url=f"https://www.cardmarket.com/en/Magic/Users/{username}")
        self.ctx.add_seller(s)
        return s

    def _country_ok(self, country: str | None) -> bool:
        return not self.allowed_countries or country is None or country in self.allowed_countries

    # ------------------------------------------------------------ saved pages
    def _load_saved_pages(self, cards):
        folder = Path(self.cfg.get("saved_pages_dir", "cardmarket_pages"))
        if not folder.is_dir():
            return
        keys = {k: c for c in cards for k in c.all_name_keys()}
        for f in sorted(folder.glob("*.htm*")):
            try:
                title, arts = parse_product_page(f.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"Could not parse {f.name}: {exc}")
                continue
            tkey = name_key(title)
            card = next((c for k, c in sorted(keys.items(), key=lambda kv: -len(kv[0]))
                         if tkey == k or tkey.startswith(k + " ")), None)
            if card is None:
                self.notes.append(f"{f.name}: '{title}' does not match any card of the decklist")
                continue
            for a in arts:
                a["set_name"] = title[len(card.name):].strip() or None
            self.saved.setdefault(card.name, []).extend(arts)
        if self.saved:
            self.notes.append(f"Cardmarket: read saved pages for {len(self.saved)} card(s).")

    def _offers_from_saved(self, card: ResolvedCard) -> list[Offer]:
        out = []
        for a in self.saved.get(card.name, []):
            if not self._country_ok(a["country"]):
                continue
            self._seller(a["seller"], a["country"])
            out.append(Offer(card=card.name, provider=self.name, seller_id=a["seller"], seller_name=a["seller"],
                             seller_country=a["country"], price_eur=a["price"], quantity=a["quantity"],
                             condition=a["condition"], language=a["language"], foil=a["foil"],
                             set_name=a.get("set_name"),
                             url=f"https://www.cardmarket.com/en/Magic/Users/{a['seller']}"))
        return out

    # ------------------------------------------------------------- estimates
    def _offers_from_guide(self, card: ResolvedCard) -> list[Offer]:
        field = self.cfg.get("priceguide_field", "trend")
        best = None
        for p in self.candidate_printings(card, None, "cardmarket_id"):
            g = self.guide.get(p.cardmarket_id)
            if not g:
                continue
            price = g.get(field) or g.get("trend") or g.get("avg")
            if price and (best is None or price < best[0]):
                best = (price, p)
        if best is None:
            return []
        price, p = best
        # one pseudo-seller per card: a conservative "you will need one more parcel" assumption
        sid = f"estimate:{card.key}"
        self.ctx.add_seller(Seller(key=f"{self.name}:{sid}", provider=self.name,
                                   name=f"Cardmarket estimate ({card.name})", country=None,
                                   shipping=ShippingRule.from_dict(self.cfg.get("estimate_seller_shipping")),
                                   estimate=True))
        return [Offer(card=card.name, provider=self.name, seller_id=sid, seller_name="Cardmarket (estimate)",
                      price_eur=round(float(price), 2), quantity=card.quantity, set_code=p.set_code,
                      set_name=p.set_name, estimate=True,
                      url=f"https://www.cardmarket.com/en/Magic/Products?idProduct={p.cardmarket_id}")]

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        offers = self._offers_from_saved(card)
        if not offers and self.guide is not None:
            offers = self._offers_from_guide(card)
        return offers
