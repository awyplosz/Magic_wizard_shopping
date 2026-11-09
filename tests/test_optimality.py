"""Verify the optimizer against exhaustive search on many small random instances.

The main instances use the real delivery costs to Paris from config.py (Cardmarket sellers in
France / Germany / elsewhere, TCGplayer and Card Kingdom imports, Paris shops with pick-up).
A smaller synthetic set covers rule features the defaults do not use (free-shipping thresholds,
odd tier tables, visit costs) so they stay correct if you configure them.

For each instance, every possible cart (every quantity from every offer) is enumerated and
priced with the reference cost model (`ShippingRule.cost`, the same function the reports use).
The MILP result must match that true optimum, so this checks both the solver formulation
and the offer pruning.
"""

import itertools
import random

import pytest

from mtg_wizard.config import DEFAULTS
from mtg_wizard.models import CardRequest, Offer, ResolvedCard
from mtg_wizard.optimizer import build_plan, optimize
from mtg_wizard.providers.base import Seller
from mtg_wizard.shipping import ShippingRule

PENALTY = 50.0

_CM = DEFAULTS["providers"]["cardmarket"]["shipping_by_country"]
PARIS_RULES = [
    ShippingRule(),                                                         # Paris shop pick-up
    ShippingRule.from_dict(_CM["FR"]),                                      # Cardmarket, France
    ShippingRule.from_dict(_CM["DE"]),                                      # Cardmarket, Germany
    ShippingRule.from_dict(_CM["default"]),                                 # Cardmarket, other EU
    ShippingRule.from_dict(DEFAULTS["providers"]["tcgplayer"]["shipping"]),    # US import
    ShippingRule.from_dict(DEFAULTS["providers"]["cardkingdom"]["shipping"]),  # US import
]


def cart_cost(plan) -> float:
    return plan.total + PENALTY * sum(plan.missing.values())


def synthetic_rule(rng: random.Random) -> ShippingRule:
    kind = rng.random()
    if kind < 0.2:  # Paris shop: free pick-up, maybe a trip cost
        return ShippingRule(visit_cost_eur=rng.choice([0.0, 0.0, 1.5]))
    caps = sorted(rng.sample([1, 2, 3, 4, 6], rng.randint(1, 3)))
    caps[-1] = 100
    costs = sorted(round(rng.uniform(0.5, 6), 2) for _ in caps)
    if rng.random() < 0.15:  # odd, non-monotone tiers must still be priced consistently
        rng.shuffle(costs)
    return ShippingRule(
        tiers=list(zip(caps, costs)),
        tracked_above_eur=rng.choice([None, 3.0, 6.0]),
        tracked_extra_eur=rng.choice([0.0, 1.0, 2.5]),
        free_above_eur=rng.choice([None, None, 8.0, 12.0]),
        import_vat_rate=rng.choice([0.0, 0.0, 0.2]),
        customs_fee_eur=rng.choice([0.0, 0.0, 2.0]),
        visit_cost_eur=0.0,
    )


def random_instance(seed: int, paris: bool = True):
    rng = random.Random(seed)
    max_price = 15.0 if paris else 5.0  # real single prices, so the 25 EUR tracked limit is crossed
    cards = [ResolvedCard(CardRequest(f"C{i}", rng.randint(1, 2)), f"C{i}") for i in range(rng.randint(2, 3))]
    sellers, offers = {}, []
    for s in range(rng.randint(2, 4)):
        key = f"m:s{s}"
        rule = rng.choice(PARIS_RULES) if paris else synthetic_rule(rng)
        sellers[key] = Seller(key, "m", f"s{s}", rule)
        for c in rng.sample(cards, rng.randint(1, len(cards))):
            for _ in range(rng.choice([1, 1, 2])):
                offers.append(Offer(c.name, "m", f"s{s}", f"s{s}", round(rng.uniform(0.2, max_price), 2),
                                    rng.randint(1, 3)))
    return cards, offers[:8], sellers


def brute_force(cards, offers, sellers) -> float:
    needs = {c.name: c.quantity for c in cards}
    ranges = [range(min(o.quantity, needs[o.card]) + 1) for o in offers]
    best = float("inf")
    for qs in itertools.product(*ranges):
        per_card = {}
        for o, q in zip(offers, qs):
            per_card[o.card] = per_card.get(o.card, 0) + q
        if any(per_card.get(c, 0) > n for c, n in needs.items()):
            continue
        best = min(best, cart_cost(build_plan("bf", list(zip(offers, qs)), sellers, needs)))
    return best


@pytest.mark.parametrize("seed", range(150))
def test_milp_matches_exhaustive_search_paris_rates(seed):
    cards, offers, sellers = random_instance(seed)
    expected = brute_force(cards, offers, sellers)
    plan = optimize(cards, offers, sellers, time_limit=30, missing_penalty=PENALTY)
    assert cart_cost(plan) == pytest.approx(expected, abs=0.011), f"seed {seed}"


@pytest.mark.parametrize("seed", range(500, 560))
def test_milp_matches_exhaustive_search_synthetic_rules(seed):
    cards, offers, sellers = random_instance(seed, paris=False)
    expected = brute_force(cards, offers, sellers)
    plan = optimize(cards, offers, sellers, time_limit=30, missing_penalty=PENALTY)
    assert cart_cost(plan) == pytest.approx(expected, abs=0.011), f"seed {seed}"


@pytest.mark.parametrize("seed", range(150, 190))
def test_milp_with_seller_cap_matches_exhaustive_search(seed):
    cards, offers, sellers = random_instance(seed)
    cap = 1 + seed % 2
    needs = {c.name: c.quantity for c in cards}
    ranges = [range(min(o.quantity, needs[o.card]) + 1) for o in offers]
    best = float("inf")
    for qs in itertools.product(*ranges):
        plan = build_plan("bf", list(zip(offers, qs)), sellers, needs)
        if len(plan.parcels) > cap or any(sum(q for o, q in zip(offers, qs) if o.card == c) > n
                                          for c, n in needs.items()):
            continue
        best = min(best, cart_cost(plan))
    plan = optimize(cards, offers, sellers, time_limit=30, missing_penalty=PENALTY, max_sellers=cap)
    assert len(plan.parcels) <= cap
    assert cart_cost(plan) == pytest.approx(best, abs=0.011), f"seed {seed}"
