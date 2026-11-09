"""Shipping-aware cart optimisation (mixed-integer linear program, solved with CBC via PuLP).

Decision variables
    x[o]   integer  copies bought from offer o           (0 <= x[o] <= stock[o])
    y[s]   binary   seller s is used (one parcel / one shop visit)
    z[s,t] binary   parcel of seller s falls in shipping tier t
    w[s]   binary   parcel value of s exceeds the "tracked shipping" threshold
    f[s]   binary   parcel value of s reaches the seller's free-shipping threshold
    S[s]   >= 0     postage paid to seller s
    u[c]   integer  copies of card c that could not be found anywhere

Minimise   sum_o price[o]*x[o] + sum_s (S[s] + customs[s] + visit[s]*y[s]) + penalty*sum_c u[c]

subject to
    sum_{o of card c} x[o] + u[c] = qty[c]                     every card is bought (or missing)
    x[o] <= stock[o] * y[seller(o)]                             can only buy from used sellers
    sum_t z[s,t] = y[s];  sum_{o of s} x[o] <= sum_t cap[t]*z[s,t]   shipping tier by #cards
    value[s] - tracked_above <= M*w[s]                          tracked shipping surcharge
    value[s] >= free_above * f[s]                               free shipping threshold
    S[s] >= sum_t cost[t]*z[s,t] + tracked_extra*w[s] - M*f[s]
    customs[s] = vat*(value[s] + S[s]) + fee*y[s]               imports from outside the EU
    sum_s y[s] <= max_sellers                                   optional

This finds the true optimum of the trade-off the Cardmarket Shopping Wizard handles badly:
cheap cards scattered over many sellers vs. few parcels with pricier cards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

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


@dataclass
class Parcel:
    seller: Seller
    lines: list[Line] = field(default_factory=list)

    @property
    def n_cards(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def items_total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
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
    by_seller: dict[str, Parcel] = {}
    got: dict[str, int] = defaultdict(int)
    for offer, q in chosen:
        if q <= 0:
            continue
        parcel = by_seller.setdefault(offer.seller_key, Parcel(sellers[offer.seller_key]))
        parcel.lines.append(Line(offer, q))
        got[offer.card] += q
    missing = {c: n - got[c] for c, n in needs.items() if got[c] < n}
    parcels = sorted(by_seller.values(), key=lambda p: (-p.total, p.seller.name))
    for p in parcels:
        p.lines.sort(key=lambda line: line.offer.card)
    return Plan(name, parcels, missing, status)


# --------------------------------------------------------------------------- pruning
def prune_offers(offers: list[Offer], needs: dict[str, int], sellers: dict[str, Seller],
                 max_per_card: int = 40, dominance: bool = True) -> list[Offer]:
    """Drop offers that can never be part of an optimal cart (or almost never).

    For a card needing q copies, let U be the cost of the q-th cheapest copy when bought
    "stand-alone" (unit price + cheapest possible fee of its seller). Any offer priced above U
    is dominated: buying the stand-alone copies instead is always at least as cheap.
    Offers from sellers carrying a single wanted card are further capped (they only compete
    on price + fee), keeping the model small even with thousands of marketplace sellers.
    """
    by_card: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        if o.card in needs:
            by_card[o.card].append(o)
    cards_per_seller: dict[str, set[str]] = defaultdict(set)
    for o in offers:
        cards_per_seller[o.seller_key].add(o.card)

    kept: list[Offer] = []
    for card, lst in by_card.items():
        q = needs[card]
        standalone = sorted(lst, key=lambda o: o.price_eur + sellers[o.seller_key].shipping.min_fixed_cost())
        acc, bound = 0, float("inf")
        for o in standalone:
            acc += o.quantity
            if acc >= q:
                bound = o.price_eur + sellers[o.seller_key].shipping.min_fixed_cost()
                break
        # With a cap on the number of sellers, a pricier copy from a seller that is used anyway
        # can be optimal, so the dominance bound does not apply.
        candidates = [o for o in lst if o.price_eur <= bound + 1e-9] if dominance else lst
        multi, single = [], []
        for o in candidates:
            if len(cards_per_seller[o.seller_key]) > 1 or sellers[o.seller_key].shipping.min_fixed_cost() == 0:
                multi.append(o)
            else:
                single.append(o)
        single.sort(key=lambda o: o.price_eur + sellers[o.seller_key].shipping.min_fixed_cost())
        acc, single_kept = 0, []
        for o in single:
            single_kept.append(o)
            acc += o.quantity
            if acc >= q + 2 or len(single_kept) >= max_per_card:
                break
        multi.sort(key=lambda o: o.price_eur)
        kept.extend(multi[: max(max_per_card * 3, 60)] + single_kept)
    return kept


# ------------------------------------------------------------------------- optimiser
def optimize(cards: list[ResolvedCard], offers: list[Offer], sellers: dict[str, Seller], *,
             time_limit: int = 120, max_sellers: int | None = None, missing_penalty: float = 500.0,
             max_offers_per_card: int = 40, msg: bool = False) -> Plan:
    needs = {c.name: c.quantity for c in cards}
    offers = prune_offers(offers, needs, sellers, max_offers_per_card, dominance=not max_sellers)
    log.info("optimizing %d offers from %d sellers", len(offers), len({o.seller_key for o in offers}))

    prob = pulp.LpProblem("mtg_cart", pulp.LpMinimize)
    x = {i: pulp.LpVariable(f"x{i}", 0, min(o.quantity, needs[o.card]), cat="Integer")
         for i, o in enumerate(offers)}
    seller_offers: dict[str, list[int]] = defaultdict(list)
    card_offers: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(offers):
        seller_offers[o.seller_key].append(i)
        card_offers[o.card].append(i)

    y = {s: pulp.LpVariable(f"y_{k}", cat="Binary") for k, s in enumerate(seller_offers)}
    u = {c: pulp.LpVariable(f"u_{k}", 0, needs[c], cat="Integer") for k, c in enumerate(needs)}

    cost_terms = [offers[i].price_eur * x[i] for i in x]
    cost_terms += [missing_penalty * u[c] for c in u]

    for c, q in needs.items():
        prob += pulp.lpSum(x[i] for i in card_offers.get(c, [])) + u[c] == q

    for k, (s, idx) in enumerate(seller_offers.items()):
        rule = sellers[s].shipping
        ys = y[s]
        for i in idx:
            prob += x[i] <= x[i].upBound * ys
        n_s = pulp.lpSum(x[i] for i in idx)
        v_s = pulp.lpSum(offers[i].price_eur * x[i] for i in idx)
        big_v = sum(offers[i].price_eur * x[i].upBound for i in idx) + 1.0

        if rule.visit_cost_eur:
            cost_terms.append(rule.visit_cost_eur * ys)
        if all(c == 0 for _, c in rule.tiers) and not rule.tracked_extra_eur:
            postage = 0
        else:
            z = [pulp.LpVariable(f"z_{k}_{t}", cat="Binary") for t in range(len(rule.tiers))]
            prob += pulp.lpSum(z) == ys
            prob += n_s <= pulp.lpSum(cap * zt for (cap, _), zt in zip(rule.tiers, z))
            fixed = pulp.lpSum(cost * zt for (_, cost), zt in zip(rule.tiers, z))
            if rule.tracked_above_eur is not None and rule.tracked_extra_eur:
                w = pulp.LpVariable(f"w_{k}", cat="Binary")
                prob += v_s - rule.tracked_above_eur <= big_v * w
                prob += w <= ys
                fixed = fixed + rule.tracked_extra_eur * w
            postage = pulp.LpVariable(f"S_{k}", 0)
            if rule.free_above_eur is not None:
                f = pulp.LpVariable(f"f_{k}", cat="Binary")
                prob += v_s >= rule.free_above_eur * f
                big_s = max(c for _, c in rule.tiers) + rule.tracked_extra_eur + 1.0
                prob += postage >= fixed - big_s * f
            else:
                prob += postage >= fixed
            cost_terms.append(postage)
        if rule.import_vat_rate or rule.customs_fee_eur:
            cost_terms.append(rule.import_vat_rate * (v_s + postage) + rule.customs_fee_eur * ys)

    if max_sellers:
        prob += pulp.lpSum(y.values()) <= max_sellers

    prob += pulp.lpSum(cost_terms)
    solver = pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit)
    prob.solve(solver)
    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal",):
        # CBC returns "Not Solved"/"Optimal" with a time limit; keep the incumbent if any.
        log.warning("solver status: %s", status)
    chosen = [(offers[i], int(round(var.value() or 0))) for i, var in x.items()]
    label = "optimal" if status == "Optimal" else f"best found ({status})"
    return build_plan("Optimized cart", chosen, sellers, needs, label)


# ------------------------------------------------------------------------- baselines
def cheapest_per_card(cards: list[ResolvedCard], offers: list[Offer], sellers: dict[str, Seller]) -> Plan:
    """What a naive 'cheapest copy of each card' approach costs once shipping is added."""
    needs = {c.name: c.quantity for c in cards}
    by_card: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        if not o.estimate:  # only real listings: estimates are not something you can click "buy" on
            by_card[o.card].append(o)
    chosen = []
    for c, q in needs.items():
        for o in sorted(by_card.get(c, []), key=lambda o: o.price_eur):
            take = min(q, o.quantity)
            chosen.append((o, take))
            q -= take
            if q <= 0:
                break
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
        chosen, covered = [], 0
        for c, q in needs.items():
            for o in sorted((o for o in lst if o.card == c), key=lambda o: o.price_eur):
                take = min(q, o.quantity)
                chosen.append((o, take))
                covered += take
                q -= take
                if q <= 0:
                    break
        plan = build_plan(f"Everything possible from one seller ({sellers[s].name})", chosen, sellers, needs)
        score = (covered, -plan.total)
        if best is None or score > best[0]:
            best = (score, plan)
    return best[1] if best else None
