import pytest

from mtg_wizard.config import DEFAULTS
from mtg_wizard.models import CardRequest, Condition, Offer, ResolvedCard
from mtg_wizard.optimizer import cheapest_per_card, optimize
from mtg_wizard.providers.base import Seller, offer_allowed
from mtg_wizard.shipping import ShippingRule


def card(name, qty=1):
    return ResolvedCard(request=CardRequest(name, qty), name=name)


# Real delivery costs to Paris (see config.py): Cardmarket sellers in France (La Poste 2026) and
# Germany (Deutsche Post 2026), Card Kingdom import, and a Paris shop with in-store pick-up.
FR = ShippingRule.from_dict(DEFAULTS["providers"]["cardmarket"]["shipping_by_country"]["FR"])
DE = ShippingRule.from_dict(DEFAULTS["providers"]["cardmarket"]["shipping_by_country"]["DE"])
CK = ShippingRule.from_dict(DEFAULTS["providers"]["cardkingdom"]["shipping"])
PICKUP = ShippingRule()


def seller(key, rule, pickup=False):
    return Seller(key=f"m:{key}", provider="m", name=key, shipping=rule, pickup=pickup)


def offer(c, s, price, qty=4, **kw):
    return Offer(card=c, provider="m", seller_id=s, seller_name=s, price_eur=price, quantity=qty, **kw)


def test_paris_rates():
    assert FR.postage(4, 10) == 1.82          # Lettre verte 20 g + 0.30 packaging
    assert FR.postage(5, 10) == 3.40          # Lettre verte 100 g + 0.30
    assert FR.postage(41, 10) == 5.74         # 250 g + 0.50
    assert FR.postage(5, 30) == pytest.approx(3.40 + 0.70)   # > 25 EUR: Lettre suivie
    assert DE.postage(4, 10) == 1.55          # Standardbrief international + 0.30
    assert DE.postage(17, 20) == 2.10         # Kompaktbrief + 0.30
    assert DE.postage(17, 30) == pytest.approx(2.10 + 3.90)  # Einschreiben international
    assert CK.cost(1, 40)["total"] == pytest.approx(15 + 0.25 * 55 + 3)  # postage + 25% VAT + 3 EUR fee
    assert PICKUP.cost(10, 50)["total"] == 0


def test_consolidates_parcels_when_shipping_dominates():
    # Cheapest copies are spread over 3 French sellers (3 letters at 1.82); one German seller has
    # all three a bit dearer and ships them in a single 1.55 letter.
    cards = [card("A"), card("B"), card("C")]
    sellers = {s.key: s for s in [seller("a", FR), seller("b", FR), seller("c", FR), seller("big", DE)]}
    offers = [offer("A", "a", 0.5), offer("B", "b", 0.5), offer("C", "c", 0.5),
              offer("A", "big", 1.0), offer("B", "big", 1.0), offer("C", "big", 1.0)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert [p.seller.name for p in plan.parcels] == ["big"]
    assert plan.total == pytest.approx(3.0 + 1.55)
    naive = cheapest_per_card(cards, offers, sellers)
    assert naive.total == pytest.approx(1.5 + 3 * 1.82)


def test_free_pickup_shop_and_missing_card():
    cards = [card("A", 2), card("B"), card("Z")]
    sellers = {s.key: s for s in [seller("paris", PICKUP, pickup=True), seller("web", FR)]}
    offers = [offer("A", "paris", 2.0, qty=1), offer("A", "web", 0.2), offer("B", "web", 0.2)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert plan.missing == {"Z": 1}
    # the French letter is needed anyway for B, so both A copies go in it (3 cards <= 4)
    assert plan.total == pytest.approx(0.6 + 1.82)


def test_splits_parcels_to_avoid_tracked_shipping():
    # 5 copies: one parcel would exceed 25 EUR and need tracked shipping (DE 33.50, FR 34.10).
    # Best: 4 cards from Germany (22 EUR, untracked letter) + 1 card from France.
    cards = [card("A", 5)]
    sellers = {s.key: s for s in [seller("de", DE), seller("fr", FR)]}
    offers = [offer("A", "de", 5.5, qty=10), offer("A", "fr", 6.0, qty=10)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert plan.total == pytest.approx(22.0 + 1.55 + 6.0 + 1.82)
    assert {p.seller.name: p.n_cards for p in plan.parcels} == {"de": 4, "fr": 1}


def test_max_sellers_constraint():
    cards = [card("A"), card("B")]
    sellers = {s.key: s for s in [seller("a", FR), seller("b", FR), seller("ab", FR)]}
    offers = [offer("A", "a", 1), offer("B", "b", 1), offer("A", "ab", 2), offer("B", "ab", 2)]
    assert optimize(cards, offers, sellers, time_limit=20).total == pytest.approx(2 + 2 * 1.82)
    assert optimize(cards, offers, sellers, time_limit=20, max_sellers=1).total == pytest.approx(4 + 1.82)


def test_offer_filters():
    cfg = {"preferences": {"languages": ["en", "fr"], "min_condition": "EX", "allow_foil": False}}
    assert offer_allowed(offer("A", "s", 1, language="fr", condition=Condition.NM), cfg)
    assert not offer_allowed(offer("A", "s", 1, language="de"), cfg)
    assert not offer_allowed(offer("A", "s", 1, condition=Condition.GD), cfg)
    assert not offer_allowed(offer("A", "s", 1, foil=True), cfg)
    assert offer_allowed(offer("A", "s", 1), cfg)  # unknown language/condition accepted by default


def test_marketplace_lists_skip_paris_pickups(tmp_path):
    from mtg_wizard.report import cards_for_marketplaces, write_marketplace_lists
    cards = [card("A"), card("B", 2), card("Z")]
    sellers = {s.key: s for s in [seller("paris", PICKUP, pickup=True), seller("web", FR)]}
    offers = [offer("A", "paris", 1.0), offer("B", "web", 0.1)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert cards_for_marketplaces(plan) == {"B": 2, "Z": 1}
    files = write_marketplace_lists(tmp_path, "deck", plan, cards)
    assert files[0].read_text(encoding="utf-8") == "2 B\n1 Z\n"
