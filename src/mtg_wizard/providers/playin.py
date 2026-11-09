"""Play-in (Paris; magicbazar.fr now redirects here).

* Card pages (/fr/carte/<id>/<slug>) are server-rendered: the Next.js payload embedded in the
  HTML contains every `CardProduct` (edition, language, grading, foil, price, stock), so a plain
  HTTP request is enough.
* The site search is JavaScript-only and protected by a bot check, so it is NOT used. The <id> of
  a card is found in Play-in's public sitemap (sitemap_index.xml, cached 7 days). That sitemap
  is incomplete, so ids can also be pinned by hand in config.yaml:

      providers:
        playin:
          card_ids: {"Sol Ring": 9710}

  (open https://www.play-in.com/fr/recherche?q=<card name>, click the card, copy the number
  from the URL /fr/carte/<number>/...).
"""

from __future__ import annotations

import json
import logging
import re

from ..models import Condition, Offer, ResolvedCard, name_key, normalize_lang, strip_accents
from ..shipping import ShippingRule
from .base import Provider, Seller

log = logging.getLogger(__name__)

BASE = "https://www.play-in.com"
SITEMAP_INDEX = BASE + "/sitemap_index.xml"


def slugify(name: str) -> str:
    s = strip_accents(name).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)


def flight_payload(html: str) -> str:
    """Concatenate the Next.js RSC 'flight' chunks embedded in a page."""
    parts = []
    for m in _PUSH.finditer(html):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return "".join(parts)


def parse_card_products(html: str) -> list[dict]:
    payload = flight_payload(html)
    dec = json.JSONDecoder()
    seen, out = set(), []
    for m in re.finditer(r'\{\s*"__typename"\s*:\s*"CardProduct"', payload):
        try:
            obj, _ = dec.raw_decode(payload, m.start())
        except ValueError:
            continue
        pid = obj.get("_id")
        if pid in seen:
            continue
        seen.add(pid)
        decl = ((obj.get("cardData") or {}).get("declination") or {})
        lang = (decl.get("lang") or {}).get("transName") if isinstance(decl.get("lang"), dict) else None
        grading = (decl.get("grading") or {}).get("name") if isinstance(decl.get("grading"), dict) else None
        ed = obj.get("linkedEdition") if isinstance(obj.get("linkedEdition"), dict) else {}
        price = obj.get("sellPrice") or obj.get("discountedPrice") or obj.get("priceWithoutDiscount")
        out.append({
            "id": pid, "name": obj.get("transName"), "qty": int(obj.get("quantity") or 0),
            "price": float(price or 0), "language": normalize_lang(lang),
            "condition": Condition.parse(grading), "foil": str(decl.get("foil") or "") not in ("", "0"),
            "set_code": (ed.get("codeName") or "").lower() or None, "set_name": ed.get("transName"),
        })
    return out


class PlayinProvider(Provider):
    name = "playin"
    label = "Play-in / Magic Bazar (Paris)"

    def prepare(self, cards):
        self.seller = Seller(key=f"{self.name}:shop", provider=self.name, name="Play-in", country="FR",
                             pickup=True, url=BASE,
                             shipping=ShippingRule(visit_cost_eur=float(self.cfg.get("visit_cost_eur", 0))))
        self.ctx.add_seller(self.seller)
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36",
                        "Accept-Language": "fr-FR,fr;q=0.9"}
        self.manual_ids = {name_key(k): v for k, v in (self.cfg.get("card_ids") or {}).items()}
        self.slug_ids = self._load_sitemap()
        self.not_found: list[str] = []

    # ----------------------------------------------------------- id lookup
    def _load_sitemap(self) -> dict[str, list[int]]:
        cached = self.http.cache_get("playin", "sitemap slugs", 24 * 7)
        if cached is not None:
            return cached
        slugs: dict[str, list[int]] = {}
        try:
            index = self.http.get(SITEMAP_INDEX).text
            for loc in re.findall(r"<loc>([^<]*/sitemaps/cartes/[^<]*)</loc>", index):
                xml = self.http.get(loc).text
                for cid, slug in re.findall(r"/fr/carte/(\d+)/([a-z0-9-]+)</loc>", xml):
                    slugs.setdefault(slug, []).append(int(cid))
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"Play-in sitemap unavailable ({exc}); only card_ids from config are used.")
            return slugs
        self.http.cache_set("playin", "sitemap slugs", slugs)
        return slugs

    def _find_card_ids(self, card: ResolvedCard) -> list[int]:
        for k in card.all_name_keys():
            if k in self.manual_ids:
                return [int(self.manual_ids[k])]
        names = [card.names.get("fr"), card.name, *card.names.values()]
        ids: list[int] = []
        for n in names:
            if n:
                for cid in self.slug_ids.get(slugify(n), []):
                    if cid not in ids:
                        ids.append(cid)
        return ids

    # --------------------------------------------------------------- offers
    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        ids = self._find_card_ids(card)
        if not ids:
            self.not_found.append(card.name)
            return []
        keys = card.all_name_keys()
        offers = []
        for cid in ids[:3]:
            url = f"{BASE}/fr/carte/{cid}/{slugify(card.names.get('fr') or card.name)}"
            html = self.http.get_text_cached("playin", url, ttl_hours=3, headers=self.headers)
            for p in parse_card_products(html):
                if p["name"] and name_key(p["name"]) not in keys:
                    continue
                if p["qty"] <= 0 or p["price"] <= 0:
                    continue
                offers.append(Offer(card=card.name, provider=self.name, seller_id="shop", seller_name="Play-in",
                                    seller_country="FR", price_eur=p["price"], quantity=p["qty"],
                                    condition=p["condition"], language=p["language"], foil=p["foil"],
                                    set_code=p["set_code"], set_name=p["set_name"], url=url))
        return offers

    def close(self):
        if self.not_found:
            self.notes.append(
                f"Play-in: {len(self.not_found)} card(s) not in Play-in's sitemap, add their id under "
                f"providers.playin.card_ids to include them: {', '.join(self.not_found[:12])}"
                + (" ..." if len(self.not_found) > 12 else ""))
