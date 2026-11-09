"""Shipping cost model per seller.

A seller's parcel costs:

    tier cost (by number of cards)                      if the seller is used
  + tracked_extra        if the parcel value > tracked_above_eur
  - (all of the above)   if the parcel value >= free_above_eur
  + import_vat_rate * (cards + shipping) + customs_fee   for imports from outside the EU
  + visit_cost           for shops you pick up from in person

This is linear (with a few binary variables), so the optimizer can reason about it exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShippingRule:
    tiers: list[tuple[int, float]] = field(default_factory=lambda: [(100000, 0.0)])
    tracked_above_eur: float | None = None
    tracked_extra_eur: float = 0.0
    free_above_eur: float | None = None
    import_vat_rate: float = 0.0
    customs_fee_eur: float = 0.0
    visit_cost_eur: float = 0.0
    label: str = ""

    @classmethod
    def from_dict(cls, d: dict | None, **extra) -> "ShippingRule":
        d = dict(d or {})
        d.update({k: v for k, v in extra.items() if v is not None})
        tiers = sorted((int(a), float(b)) for a, b in d.get("tiers", [[100000, 0.0]]))
        return cls(
            tiers=tiers,
            tracked_above_eur=d.get("tracked_above_eur"),
            tracked_extra_eur=float(d.get("tracked_extra_eur", 0.0)),
            free_above_eur=d.get("free_above_eur"),
            import_vat_rate=float(d.get("import_vat_rate", 0.0)),
            customs_fee_eur=float(d.get("customs_fee_eur", 0.0)),
            visit_cost_eur=float(d.get("visit_cost_eur", 0.0)),
            label=d.get("label", ""),
        )

    @property
    def is_free(self) -> bool:
        return (all(c == 0 for _, c in self.tiers) and self.tracked_extra_eur == 0
                and self.import_vat_rate == 0 and self.customs_fee_eur == 0)

    @property
    def max_cards(self) -> int:
        return max(n for n, _ in self.tiers)

    def min_fixed_cost(self) -> float:
        """Cheapest possible cost of using this seller at all (used for pruning)."""
        if self.free_above_eur is not None:
            return self.visit_cost_eur
        return min(c for _, c in self.tiers) + self.customs_fee_eur + self.visit_cost_eur

    def postage(self, n_cards: int, value_eur: float) -> float:
        """Postage only (no customs, no visit cost)."""
        if n_cards <= 0:
            return 0.0
        if self.free_above_eur is not None and value_eur >= self.free_above_eur:
            return 0.0
        cost = next((c for n, c in self.tiers if n_cards <= n), self.tiers[-1][1])
        if self.tracked_above_eur is not None and value_eur > self.tracked_above_eur:
            cost += self.tracked_extra_eur
        return cost

    def cost(self, n_cards: int, value_eur: float) -> dict[str, float]:
        """Full breakdown for a parcel of n_cards worth value_eur."""
        if n_cards <= 0:
            return {"postage": 0.0, "customs": 0.0, "visit": 0.0, "total": 0.0}
        postage = self.postage(n_cards, value_eur)
        customs = self.import_vat_rate * (value_eur + postage) + self.customs_fee_eur \
            if (self.import_vat_rate or self.customs_fee_eur) else 0.0
        visit = self.visit_cost_eur
        return {"postage": postage, "customs": customs, "visit": visit,
                "total": postage + customs + visit}
