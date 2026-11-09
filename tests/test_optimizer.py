from mtg_wizard.models import CardRequest, Condition, Offer, ResolvedCard
from mtg_wizard.optimizer import cheapest_per_card, optimize
from mtg_wizard.providers.base import Seller, offer_allowed
from mtg_wizard.shipping import ShippingRule


def card(name, qty=1):
    return ResolvedCard(request=CardRequest(name, qty), name=name)


def seller(key, ship=0.0, **kw):
    rule = ShippingRule(tiers=[(100000, ship)], **kw)
    return Seller(key=f"m:{key}", provider="m", name=key, shipping=rule)


def offer(c, s, price, qty=4, **kw):
    return Offer(card=c, provider="m", seller_id=s, seller_name=s, price_eur=price, quantity=qty, **kw)


def test_consolidates_parcels_when_shipping_dominates():
    # Scattered cheapest copies: 3 sellers x 3 EUR shipping. One seller has all three a bit dearer.
    cards = [card("A"), card("B"), card("C")]
    sellers = {s.key: s for s in [seller("a", 3), seller("b", 3), seller("c", 3), seller("big", 3)]}
    offers = [offer("A", "a", 0.5), offer("B", "b", 0.5), offer("C", "c", 0.5),
              offer("A", "big", 1.0), offer("B", "big", 1.0), offer("C", "big", 1.0)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert [p.seller.name for p in plan.parcels] == ["big"]
    assert plan.total == 6.0  # 3 + 3 shipping
    naive = cheapest_per_card(cards, offers, sellers)
    assert naive.total == 10.5  # 1.5 + 3 parcels x 3


def test_free_pickup_shop_and_missing_card():
    cards = [card("A", 2), card("B"), card("Z")]
    sellers = {s.key: s for s in [seller("paris", 0), seller("web", 5)]}
    offers = [offer("A", "paris", 2.0, qty=1), offer("A", "web", 0.2), offer("B", "web", 0.2)]
    plan = optimize(cards, offers, sellers, time_limit=20)
    assert plan.missing == {"Z": 1}
    # web parcel is needed anyway for B, so both A copies go there
    assert plan.total == 0.6 + 5


def test_tiers_tracked_and_free_shipping():
    rule = ShippingRule(tiers=[(4, 1.0), (100, 3.0)], tracked_above_eur=25, tracked_extra_eur=2,
                        free_above_eur=100)
    assert rule.postage(3, 10) == 1.0
    assert rule.postage(5, 10) == 3.0
    assert rule.postage(5, 30) == 5.0
    assert rule.postage(5, 150) == 0.0
    imp = ShippingRule(tiers=[(100, 10.0)], import_vat_rate=0.2, customs_fee_eur=5)
    assert imp.cost(1, 40)["total"] == 10 + 0.2 * 50 + 5

    cards = [card("A", 5)]
    sellers = {"m:s": Seller(key="m:s", provider="m", name="s", shipping=rule)}
    plan = optimize(cards, [offer("A", "s", 6.0, qty=10)], sellers, time_limit=20)
    # 5 cards, value 30 -> tier 2 (3.0) + tracked (2.0)
    assert plan.total == 30 + 5.0
    assert plan.fees_total == 5.0


def test_max_sellers_constraint():
    cards = [card("A"), card("B")]
    sellers = {s.key: s for s in [seller("a", 0), seller("b", 0), seller("ab", 0)]}
    offers = [offer("A", "a", 1), offer("B", "b", 1), offer("A", "ab", 2), offer("B", "ab", 2)]
    assert optimize(cards, offers, sellers, time_limit=20).total == 2
    assert optimize(cards, offers, sellers, time_limit=20, max_sellers=1).total == 4


def test_offer_filters():
    cfg = {"preferences": {"languages": ["en", "fr"], "min_condition": "EX", "allow_foil": False}}
    assert offer_allowed(offer("A", "s", 1, language="fr", condition=Condition.NM), cfg)
    assert not offer_allowed(offer("A", "s", 1, language="de"), cfg)
    assert not offer_allowed(offer("A", "s", 1, condition=Condition.GD), cfg)
    assert not offer_allowed(offer("A", "s", 1, foil=True), cfg)
    assert offer_allowed(offer("A", "s", 1), cfg)  # unknown language/condition accepted by default
