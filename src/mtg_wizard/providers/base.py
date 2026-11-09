"""Provider base class: each shop/marketplace turns resolved cards into Offers + Sellers.

A provider must never crash the whole run: errors are caught per card and reported,
and a card that a shop does not stock simply yields no offer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..http import HttpClient
from ..models import Condition, Offer, Printing, ResolvedCard, normalize_lang
from ..shipping import ShippingRule

log = logging.getLogger(__name__)


@dataclass
class Seller:
    key: str  # "<provider>:<seller_id>"
    provider: str
    name: str
    shipping: ShippingRule
    country: str | None = None
    pickup: bool = False  # Paris shop: you pick the cards up in store
    url: str | None = None
    estimate: bool = False


@dataclass
class Context:
    cfg: dict
    http: HttpClient
    usd_to_eur: float
    sellers: dict[str, Seller] = field(default_factory=dict)

    def add_seller(self, seller: Seller) -> None:
        self.sellers.setdefault(seller.key, seller)


@dataclass
class ProviderResult:
    provider: str
    offers: list[Offer] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # card name -> error message
    disabled_reason: str | None = None
    notes: list[str] = field(default_factory=list)


class Provider:
    name = "base"
    label = "Base"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.cfg: dict = ctx.cfg.get("providers", {}).get(self.name, {})
        self.http = ctx.http
        self.notes: list[str] = []

    # Subclasses override -------------------------------------------------
    def prepare(self, cards: list[ResolvedCard]) -> None:
        """Called once before fetching (download price lists, check credentials...).
        Raise ProviderUnavailable to skip the provider cleanly."""

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # Driver ----------------------------------------------------------------
    def fetch(self, cards: list[ResolvedCard], progress=None) -> ProviderResult:
        res = ProviderResult(self.name)
        try:
            self.prepare(cards)
        except ProviderUnavailable as exc:
            res.disabled_reason = str(exc)
            return res
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: preparation failed", self.label)
            res.disabled_reason = f"preparation failed: {exc}"
            return res
        try:
            for i, card in enumerate(cards):
                if progress:
                    progress(self.label, i + 1, len(cards), card.name)
                try:
                    res.offers.extend(self.fetch_card(card))
                except ProviderUnavailable as exc:
                    res.disabled_reason = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001 - one card must not stop the run
                    log.debug("%s: %s failed", self.label, card.name, exc_info=True)
                    res.errors[card.name] = f"{type(exc).__name__}: {exc}"
        finally:
            self.close()
        res.notes.extend(self.notes)
        return res

    # Helpers -------------------------------------------------------------
    @property
    def prefs(self) -> dict:
        return self.ctx.cfg.get("preferences", {})

    def candidate_printings(self, card: ResolvedCard, max_n: int | None = None,
                            id_attr: str | None = None, currency: str = "eur") -> list[Printing]:
        prints = list(card.printings)
        if id_attr:
            prints = [p for p in prints if getattr(p, id_attr)]
        req = card.request
        if req.set_code and self.prefs.get("respect_requested_printing"):
            prints = [p for p in prints if p.set_code.lower() == req.set_code.lower()]
        price_attr = "price_eur" if currency == "eur" else "price_usd"
        prints.sort(key=lambda p: (getattr(p, price_attr) is None, getattr(p, price_attr) or 0))
        return prints[:max_n] if max_n else prints


class ProviderUnavailable(Exception):
    """The provider cannot run at all (no credentials, site down, missing dependency...)."""


def offer_allowed(offer: Offer, cfg: dict, card: ResolvedCard | None = None) -> bool:
    """Apply the user's preferences (language, condition, foil, printing)."""
    prefs = cfg.get("preferences", {})
    if offer.quantity <= 0 or offer.price_eur is None or offer.price_eur <= 0:
        return False
    langs = {normalize_lang(x) for x in prefs.get("languages") or []}
    if langs:
        if offer.language is None:
            if not prefs.get("unknown_language_ok", True):
                return False
        elif normalize_lang(offer.language) not in langs:
            return False
    min_cond = Condition.parse(prefs.get("min_condition"))
    if min_cond:
        if offer.condition is None:
            if not prefs.get("unknown_condition_ok", True):
                return False
        elif offer.condition < min_cond:
            return False
    if offer.foil and not prefs.get("allow_foil", True):
        return False
    if card is not None:
        req = card.request
        if req.foil is True and not offer.foil:
            return False
        if req.set_code and prefs.get("respect_requested_printing") and offer.set_code \
                and offer.set_code.lower() != req.set_code.lower():
            return False
    return True
