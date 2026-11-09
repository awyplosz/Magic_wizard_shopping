"""Shipping cost model per seller.

A seller's parcel costs:

    tier cost: cheapest tier whose max_cards >= cards in the parcel    if the seller is used
  + tracked_extra        if the parcel value > tracked_above_eur
  - (all of the above)   if the parcel value >= free_above_eur
  + import_vat_rate * (cards + postage) + customs_fee   for imports from outside the EU
  + visit_cost           for shops you pick up from in person

This is linear (with a few binary variables), so the optimizer reasons about it exactly; the
optimizer and the reports both price parcels with `cost()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ShippingRule:
    tiers: tuple[tuple[int, float], ...] = ((100000, 0.0),)
    tracked_above_eur: float | None = None
    tracked_extra_eur: float = 0.0
    free_above_eur: float | None = None
    import_vat_rate: float = 0.0
    customs_fee_eur: float = 0.0
    visit_cost_eur: float = 0.0
    _effective: tuple[tuple[int, float], ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "tiers", tuple(sorted((int(n), float(c)) for n, c in self.tiers)))
        # A bigger envelope can always carry fewer cards, so the price for n cards is the cheapest
        # tier with room for n. Keep only tiers that are cheaper than every larger one.
        eff: list[tuple[int, float]] = []
        for cap, cost in reversed(self.tiers):
            if not eff or cost < eff[-1][1]:
                eff.append((cap, cost))
        object.__setattr__(self, "_effective", tuple(reversed(eff)))

    @classmethod
    def from_dict(cls, d: dict | None, **extra) -> ShippingRule:
        d = {**(d or {}), **{k: v for k, v in extra.items() if v is not None}}
        return cls(
            tiers=tuple(tuple(t) for t in d.get("tiers", [[100000, 0.0]])),
            tracked_above_eur=d.get("tracked_above_eur"),
            tracked_extra_eur=float(d.get("tracked_extra_eur", 0.0)),
            free_above_eur=d.get("free_above_eur"),
            import_vat_rate=float(d.get("import_vat_rate", 0.0)),
            customs_fee_eur=float(d.get("customs_fee_eur", 0.0)),
            visit_cost_eur=float(d.get("visit_cost_eur", 0.0)),
        )

    def effective_tiers(self) -> tuple[tuple[int, float], ...]:
        """(max_cards, cost) with cost strictly increasing with max_cards."""
        return self._effective

    @property
    def is_free(self) -> bool:
        """No postage and no import costs at all (a visit cost may still apply)."""
        return (not any(c for _, c in self._effective) and not self.tracked_extra_eur
                and not self.import_vat_rate and not self.customs_fee_eur)

    def postage(self, n_cards: int, value_eur: float) -> float:
        """Postage only (no customs, no visit cost)."""
        if n_cards <= 0:
            return 0.0
        if self.free_above_eur is not None and value_eur >= self.free_above_eur:
            return 0.0
        cost = next((c for n, c in self._effective if n_cards <= n), self._effective[-1][1])
        if self.tracked_above_eur is not None and value_eur > self.tracked_above_eur:
            cost += self.tracked_extra_eur
        return cost

    def cost(self, n_cards: int, value_eur: float) -> dict[str, float]:
        """Full breakdown for a parcel of n_cards worth value_eur."""
        if n_cards <= 0:
            return {"postage": 0.0, "customs": 0.0, "visit": 0.0, "total": 0.0}
        postage = self.postage(n_cards, value_eur)
        customs = self.import_vat_rate * (value_eur + postage) + self.customs_fee_eur
        visit = self.visit_cost_eur
        return {"postage": postage, "customs": customs, "visit": visit, "total": postage + customs + visit}
