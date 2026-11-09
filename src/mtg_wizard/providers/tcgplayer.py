"""TCGplayer (USA marketplace).

TCGplayer's official API is reserved to partners and its website endpoints refuse automated
clients, so this tool does not scrape tcgplayer.com. It uses the TCGplayer *market price*
that Scryfall republishes for every printing (refreshed daily) as an ESTIMATE, priced as one
extra parcel from the USA (international postage + French import VAT + carrier fee).

In practice TCGplayer is rarely competitive for a buyer in France (most sellers do not ship
abroad and imports pay VAT), so these estimates mainly serve as a reference point.
"""

from __future__ import annotations

from ..models import Offer, ResolvedCard
from ..shipping import ShippingRule
from .base import Provider, Seller


class TCGplayerProvider(Provider):
    name = "tcgplayer"
    label = "TCGplayer"

    def prepare(self, cards):
        self.shipping = ShippingRule.from_dict(self.cfg.get("shipping"))
        if not self.ctx.cfg.get("solver", {}).get("include_estimates", True):
            from .base import ProviderUnavailable
            raise ProviderUnavailable("only estimates available and --no-estimates is set")
        self.notes.append("TCGplayer: market-price estimates (via Scryfall), not real listings.")

    def fetch_card(self, card: ResolvedCard) -> list[Offer]:
        prints = [p for p in self.candidate_printings(card, None, "tcgplayer_id", "usd") if p.price_usd]
        if not prints:
            return []
        p = prints[0]
        sid = f"estimate:{card.key}"
        self.ctx.add_seller(Seller(key=f"{self.name}:{sid}", provider=self.name,
                                   name=f"TCGplayer estimate ({card.name})", country="US",
                                   shipping=self.shipping, estimate=True))
        return [Offer(card=card.name, provider=self.name, seller_id=sid, seller_name="TCGplayer (estimate)",
                      seller_country="US", price_eur=round(p.price_usd * self.ctx.usd_to_eur, 2),
                      quantity=card.quantity, language="en", set_code=p.set_code, set_name=p.set_name,
                      estimate=True, original_price=p.price_usd, original_currency="USD",
                      url=f"https://www.tcgplayer.com/product/{p.tcgplayer_id}")]
