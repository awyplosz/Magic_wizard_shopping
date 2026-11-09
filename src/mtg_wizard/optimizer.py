"""Shipping-aware cart optimisation (mixed-integer linear program, solved with CBC via PuLP).

Decision variables
    x[o]   integer  copies bought from offer o           (0 <= x[o] <= min(stock, need))
    y[s]   binary   seller s is used (one parcel / one shop visit)
    b[s,t] binary   parcel of seller s holds more than cap[t] cards (b[s,t] <= b[s,t-1] <= y[s])
    w[s]   binary   parcel value of s exceeds the "tracked shipping" threshold
    f[s]   binary   parcel value of s reaches the seller's free-shipping threshold
    u[c]   integer  copies of card c that could not be found anywhere

    postage[s] = cost[0]*y[s] + sum_t (cost[t+1]-cost[t])*b[s,t] + tracked_extra*w[s]
                 (minus all of it when f[s] = 1)

Minimise   sum_o price[o]*x[o] + sum_s (postage[s] + customs[s] + visit[s]*y[s]) + penalty*sum_c u[c]

subject to
    sum_{o of card c} x[o] + u[c] = need[c]                    every card is bought (or missing)
    x[o] <= ub[o] * y[seller(o)]                                 only buy from sellers you use
    cards[s] - cap[t] <= (max_cards[s] - cap[t]) * b[s,t]        step to the next shipping tier
    value[s] - tracked_above <= (max_value[s] - tracked_above) * w[s]
    value[s] >= free_above * f[s]                                free shipping threshold
    customs[s] = vat*(value[s] + postage[s]) + fee*y[s]          imports from outside the EU
    sum_s y[s] <= max_sellers                                    optional

Binaries are only created for thresholds a parcel can actually cross, with the tightest
big-M values, which keeps the LP relaxation strong and the solve fast.

The objective is exactly the cost computed by `ShippingRule.cost` for the chosen parcels;
tests/test_optimality.py checks this against exhaustive search on random instances.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property

import pulp

from .models import Offer, ResolvedCard
from .providers.base import Seller

log = logging.getLogger(__name__)


@dataclass
class Line:
    offer: Offer
    quantity: int

    @property
    def total(self) -> float:
        return round(self.offer.price_eur * self.quantity, 2)


@dataclass(eq=False)
class Parcel:
    """All cards bought from one seller. Built once by `build_plan`, then read-only."""

    seller: Seller
    lines: list[Line] = field(default_factory=list)

    @cached_property
    def n_cards(self) -> int:
        return sum(line.quantity for line in self.lines)

    @cached_property
    def items_total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @cached_property
    def fees(self) -> dict[str, float]:
        return self.seller.shipping.cost(self.n_cards, self.items_total)

    @property
    def total(self) -> float:
        return round(self.items_total + self.fees["total"], 2)


@dataclass
class Plan:
    name: str
    parcels: list[Parcel]
    missing: dict[str, int]  # card -> copies not found
    status: str = "optimal"

    @property
    def items_total(self) -> float:
        return round(sum(p.items_total for p in self.parcels), 2)

    @property
    def fees_total(self) -> float:
        return round(sum(p.fees["total"] for p in self.parcels), 2)

    @property
    def total(self) -> float:
        return round(self.items_total + self.fees_total, 2)

    @property
    def n_cards(self) -> int:
        return sum(p.n_cards for p in self.parcels)

    @property
    def uses_estimates(self) -> bool:
        return any(line.offer.estimate for p in self.parcels for line in p.lines)


def build_plan(name: str, chosen: list[tuple[Offer, int]], sellers: dict[str, Seller],
               needs: dict[str, int], status: str = "optimal") -> Plan:
    lines: dict[str, list[Line]] = defaultdict(list)
    got: dict[str, int] = defaultdict(int)
    for offer, q in chosen:
        if q > 0:
            lines[offer.seller_key].append(Line(offer, q))
            got[offer.card] += q
    parcels = [Parcel(sellers[s], sorted(ls, key=lambda line: line.offer.card)) for s, ls in lines.items()]
    parcels.sort(key=lambda p: (-p.total, p.seller.name))
    missing = {c: n - got[c] for c, n in needs.items() if got[c] < n}
    return Plan(name, parcels, missing, status)


# --------------------------------------------------------------------------- pruning
def prune_offers(offers: list[Offer], needs: dict[str, int], sellers: dict[str, Seller],
                 seller_cap: bool = False) -> list[Offer]:
    """Remove offers that provably cannot improve the cart.

    Within one seller and one card, a pricier copy is never needed once cheaper copies of the
    same seller cover the need: swapping it for a cheaper one keeps the parcel's card count and
    lowers its value, so postage tier, tracked surcharge and VAT can only go down. The one
    exception is a free-shipping threshold (a pricier card can reach it), so sellers with
    such a threshold are left untouched. Offers for cards not in the list are dropped.
    """
    groups: dict[tuple[str, str], list[Offer]] = defaultdict(list)
    for o in offers:
        if o.card in needs and o.quantity > 0:
            groups[(o.seller_key, o.card)].append(o)

    # Free pick-up shops (no postage, no visit cost): if they hold enough copies of a card at a
    # price <= p, a copy priced above p elsewhere is never needed (some free copy at <= p is
    # unused and can replace it; the other parcel only gets lighter and cheaper). Not valid
    # with a cap on the number of sellers, where the free shop would count as one more seller.
    free_price_cap: dict[str, float] = {}
    free_copies: dict[str, list[Offer]] = defaultdict(list)
    for (seller_key, card), lst in groups.items():
        rule = sellers[seller_key].shipping
        if rule.is_free and not rule.visit_cost_eur:
            free_copies[card].extend(lst)
    for card, lst in ({} if seller_cap else free_copies).items():
        covered = 0
        for o in sorted(lst, key=lambda o: o.price_eur):
            covered += o.quantity
            if covered >= needs[card]:
                free_price_cap[card] = o.price_eur
                break

    kept: list[Offer] = []
    for (seller_key, card), lst in groups.items():
        rule = sellers[seller_key].shipping
        if not (rule.is_free and not rule.visit_cost_eur) and card in free_price_cap \
                and rule.free_above_eur is None:
            lst = [o for o in lst if o.price_eur <= free_price_cap[card]]
        if rule.free_above_eur is not None:
            kept.extend(lst)
            continue
        lst.sort(key=lambda o: o.price_eur)
        covered = 0
        for o in lst:
            kept.append(o)
            covered += o.quantity
            if covered >= needs[card]:
                break
    return kept


# ------------------------------------------------------------------------- optimiser
def optimize(cards: list[ResolvedCard], offers: list[Offer], sellers: dict[str, Seller], *,
             time_limit: int = 120, max_sellers: int | None = None, missing_penalty: float = 500.0,
             msg: bool = False) -> Plan:
    needs = {c.name: c.quantity for c in cards}
    offers = prune_offers(offers, needs, sellers, seller_cap=bool(max_sellers))
    log.info("optimizing %d offers from %d sellers", len(offers), len({o.seller_key for o in offers}))

    prob = pulp.LpProblem("mtg_cart", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x{i}", 0, min(o.quantity, needs[o.card]), cat="Integer")
         for i, o in enumerate(offers)]
    u = {c: pulp.LpVariable(f"u{k}", 0, q, cat="Integer") for k, (c, q) in enumerate(needs.items())}
    by_seller: dict[str, list[int]] = defaultdict(list)
    by_card: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(offers):
        by_seller[o.seller_key].append(i)
        by_card[o.card].append(i)

    cost = [o.price_eur * x[i] for i, o in enumerate(offers)]
    cost += [missing_penalty * v for v in u.values()]
    for c, q in needs.items():
        prob += pulp.lpSum(x[i] for i in by_card[c]) + u[c] == q

    used = []  # y variables, for the max_sellers cap
    for k, (s, idx) in enumerate(by_seller.items()):
        rule = sellers[s].shipping
        if rule.is_free and not rule.visit_cost_eur and not max_sellers:
            continue  # free pick-up shop: buying there has no fixed cost, no need for y
        y = pulp.LpVariable(f"y{k}", cat="Binary")
        used.append(y)
        for i in idx:
            prob += x[i] <= x[i].upBound * y
        if rule.visit_cost_eur:
            cost.append(rule.visit_cost_eur * y)

        n_s = pulp.lpSum(x[i] for i in idx)
        v_s = pulp.lpSum(offers[i].price_eur * x[i] for i in idx)
        n_max = sum(x[i].upBound for i in idx)  # most cards this parcel can ever hold
        v_max = sum(offers[i].price_eur * x[i].upBound for i in idx)
        tiers = rule.effective_tiers()
        if n_max > tiers[-1][0]:
            prob += n_s <= tiers[-1][0] * y

        # Incremental tiers: base cost when used, plus one "step up" binary per tier boundary the
        # parcel can actually cross, with tight big-M values (much stronger LP relaxation than
        # choosing one tier among caps up to 100000).
        postage = tiers[0][1] * y
        prev = None
        for t, (cap, c) in enumerate(tiers[:-1]):
            if cap >= n_max:
                break
            b = pulp.LpVariable(f"b{k}_{t}", cat="Binary")
            prob += n_s - cap <= (n_max - cap) * b
            prob += b <= (prev if prev is not None else y)
            postage += (tiers[t + 1][1] - c) * b
            prev = b
        if rule.tracked_above_eur is not None and rule.tracked_extra_eur and v_max > rule.tracked_above_eur:
            w = pulp.LpVariable(f"w{k}", cat="Binary")
            prob += v_s - rule.tracked_above_eur <= (v_max - rule.tracked_above_eur) * w
            prob += w <= y
            postage += rule.tracked_extra_eur * w
        if rule.free_above_eur is not None and v_max >= rule.free_above_eur:
            f = pulp.LpVariable(f"f{k}", cat="Binary")
            prob += v_s >= rule.free_above_eur * f
            prob += f <= y
            paid = pulp.LpVariable(f"P{k}", 0)
            prob += paid >= postage - (tiers[-1][1] + rule.tracked_extra_eur) * f
            postage = paid
        cost.append(postage)
        if rule.import_vat_rate or rule.customs_fee_eur:
            cost.append(rule.import_vat_rate * (v_s + postage) + rule.customs_fee_eur * y)

    if max_sellers:
        prob += pulp.lpSum(used) <= max_sellers

    prob += pulp.lpSum(cost)
    prob.solve(pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":  # time limit reached: CBC keeps the best cart found so far
        log.warning("solver status: %s", status)
    chosen = [(o, int(round(x[i].value() or 0))) for i, o in enumerate(offers)]
    plan = build_plan("Optimized cart", chosen, sellers, needs,
                      "optimal" if status == "Optimal" else f"best found ({status})")
    # Safety net: the solver's objective must equal the cost model used in the report.
    objective = pulp.value(prob.objective) or 0.0
    expected = plan.total + missing_penalty * sum(plan.missing.values())
    if status == "Optimal" and abs(objective - expected) > 0.02 * (1 + len(plan.parcels)):
        log.warning("solver objective %.2f differs from recomputed cost %.2f", objective, expected)
    return plan


# ------------------------------------------------------------------------- baselines
def _greedy(needs: dict[str, int], offers: list[Offer]) -> list[tuple[Offer, int]]:
    """Take the cheapest copies of each card from `offers` until the need is covered."""
    by_card: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        by_card[o.card].append(o)
    chosen = []
    for c, q in needs.items():
        for o in sorted(by_card.get(c, []), key=lambda o: o.price_eur):
            if q <= 0:
                break
            take = min(q, o.quantity)
            chosen.append((o, take))
            q -= take
    return chosen


def cheapest_per_card(cards: list[ResolvedCard], offers: list[Offer], sellers: dict[str, Seller]) -> Plan:
    """What a naive 'cheapest copy of each card' approach costs once shipping is added.
    Only real listings: estimates are not something you can click "buy" on."""
    needs = {c.name: c.quantity for c in cards}
    chosen = _greedy(needs, [o for o in offers if not o.estimate])
    return build_plan("Cheapest copy of each card (shipping ignored when choosing)", chosen, sellers, needs)


def best_single_seller(cards: list[ResolvedCard], offers: list[Offer], sellers: dict[str, Seller]) -> Plan | None:
    """The single seller covering the most cards (then the cheapest) - the 'one parcel' extreme."""
    needs = {c.name: c.quantity for c in cards}
    by_seller: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        if not o.estimate:
            by_seller[o.seller_key].append(o)
    best = None
    for s, lst in by_seller.items():
        plan = build_plan(f"Everything possible from one seller ({sellers[s].name})",
                          _greedy(needs, lst), sellers, needs)
        score = (plan.n_cards, -plan.total)
        if best is None or score > best[0]:
            best = (score, plan)
    return best[1] if best else None
