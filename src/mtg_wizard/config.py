"""Configuration: built-in defaults deep-merged with an optional user YAML file."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

# Shipping tiers are [max_cards, cost_eur]: the cheapest tier whose max_cards >= number of
# cards in the parcel applies. All shipping numbers are ESTIMATES - check them against
# what the sites actually charge you and adjust them in config.yaml.
EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
      "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]

# Cardmarket parcels delivered to Paris. Cardmarket's help: a 20 g letter holds 4 cards, 50 g 17,
# 100 g 40 (larger steps extrapolated: ~100 cards per 250 g, ~200 per 500 g, ~400 per 1 kg); it
# adds a packaging fee of 0.30 EUR (letter < 100 g) or 0.50 EUR (> 100 g or tracked), and
# tracked shipping is mandatory above 25 EUR. tracked_extra = tracked total - untracked total.
#
# French seller -> Paris: La Poste 2026, Lettre verte 1.52 (20 g) / 3.10 (100 g) / 5.24 (250 g)
# / 7.41 (500 g) / 9.29 (1 kg) / 11.14 (2 kg); Lettre suivie = Lettre verte + 0.50.
FR_TO_PARIS = {
    "tiers": [[4, 1.82], [40, 3.40], [100, 5.74], [200, 7.91], [400, 9.79], [100000, 11.64]],
    "tracked_above_eur": 25.0, "tracked_extra_eur": 0.70,
}
# German seller -> Paris: Deutsche Post Brief International (price list of 01.09.2026),
# Standard 1.25 (20 g) / Kompakt 1.80 (50 g) / Gross 3.30 (500 g) / Maxi 6.50 (1 kg) / 17.00 (2 kg);
# Einschreiben International +3.70.
DE_TO_PARIS = {
    "tiers": [[4, 1.55], [17, 2.10], [40, 3.60], [200, 3.80], [400, 7.00], [100000, 17.50]],
    "tracked_above_eur": 25.0, "tracked_extra_eur": 3.90,
}

DEFAULTS: dict = {
    "home_country": "FR",
    "preferences": {
        "languages": ["en", "fr"],     # acceptable card languages
        "min_condition": "EX",         # MT, NM, EX, GD, LP, PL, PO
        "allow_foil": True,            # foils are accepted when they are cheaper
        "respect_requested_printing": False,  # True = only the (SET) given in the decklist
        "unknown_language_ok": True,   # shops that do not state the language
        "unknown_condition_ok": True,
    },
    "currency": {"usd_to_eur": None, "fallback_usd_to_eur": 0.86},
    "solver": {
        "time_limit_s": 120,
        "max_sellers": None,           # cap the number of parcels / shops
        "missing_card_penalty_eur": 500.0,
        "include_estimates": True,     # use Cardmarket price-guide estimates as offers
    },
    "http": {"cache_dir": ".cache", "default_delay_s": 0.6},
    "providers": {
        # Card Kingdom paused all shipping to the EU (incl. France) in Nov 2026, so it is off by
        # default. When it ships, it charges 25% VAT + a 3 EUR customs fee (its own support page);
        # its postage is not published, the tiers below are an estimate.
        "cardkingdom": {
            "enabled": False,
            "shipping": {"tiers": [[20, 15.0], [60, 22.0], [100000, 35.0]],
                         "import_vat_rate": 0.25, "customs_fee_eur": 3.0},
        },
        # Imports from the US into France: 20% French VAT + the EU 3 EUR customs fee on parcels
        # under 150 EUR. TCGplayer international postage is not published: estimate.
        "tcgplayer": {
            "enabled": True,
            "shipping": {"tiers": [[10, 7.0], [40, 12.0], [100000, 20.0]],
                         "import_vat_rate": 0.20, "customs_fee_eur": 3.0},
        },
        "cardmarket": {
            "enabled": True,
            # "priceguide": public daily price guide (estimates, no seller detail)
            # "saved_pages": product pages you saved yourself from your browser
            "modes": ["priceguide", "saved_pages"],
            "priceguide_field": "trend",     # low | trend | avg | avg7 | avg30
            "saved_pages_dir": "cardmarket_pages",
            "seller_countries": EU,
            # Price-guide estimates: assume one more parcel from a French seller.
            "estimate_seller_shipping": FR_TO_PARIS,
            # Delivery to Paris from each seller country (Cardmarket uses the national post).
            "shipping_by_country": {
                "FR": FR_TO_PARIS,
                "DE": DE_TO_PARIS,
                # Other countries: not verified, rough estimate - adjust in config.yaml.
                "default": {"tiers": [[4, 2.2], [17, 3.6], [40, 6.5], [400, 14.0], [100000, 20.0]],
                            "tracked_above_eur": 25.0, "tracked_extra_eur": 4.0},
            },
        },
        # Paris shops: in-store pick-up, so no shipping. `visit_cost_eur` lets you put a
        # price on the trip itself (metro ticket, time...) to avoid visiting 4 shops for 1 card.
        "playin": {"enabled": True, "visit_cost_eur": 0.0, "card_ids": {}},
        "parkage": {"enabled": True, "visit_cost_eur": 0.0},
        "troll2jeux": {"enabled": True, "visit_cost_eur": 0.0, "default_language": "fr"},
        "csv": {"enabled": True, "files": [], "visit_cost_eur": 0.0},
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path is None:
        for candidate in ("config.yaml", "config.yml"):
            if Path(candidate).exists():
                path = candidate
                break
    if path:
        with open(path, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        cfg = deep_merge(cfg, user)
    return cfg
