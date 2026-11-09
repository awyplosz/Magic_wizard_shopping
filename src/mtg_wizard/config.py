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
        "cardkingdom": {
            "enabled": True,
            "shipping": {"tiers": [[20, 15.0], [60, 22.0], [100000, 35.0]],
                         "import_vat_rate": 0.20, "customs_fee_eur": 10.0},
        },
        "tcgplayer": {
            "enabled": True,
            "shipping": {"tiers": [[10, 7.0], [40, 12.0], [100000, 20.0]],
                         "import_vat_rate": 0.20, "customs_fee_eur": 10.0},
        },
        "cardmarket": {
            "enabled": True,
            # "priceguide": public daily price guide (estimates, no seller detail)
            # "saved_pages": product pages you saved yourself from your browser
            "modes": ["priceguide", "saved_pages"],
            "priceguide_field": "trend",     # low | trend | avg | avg7 | avg30
            "saved_pages_dir": "cardmarket_pages",
            "seller_countries": EU,
            "estimate_seller_shipping": {"tiers": [[4, 1.6], [17, 3.0], [40, 5.5], [100000, 12.0]],
                                         "tracked_above_eur": 25.0, "tracked_extra_eur": 3.0},
            # cost from each seller country to France (letter / small parcel)
            "shipping_by_country": {
                "FR": {"tiers": [[4, 1.5], [17, 2.6], [40, 4.8], [100000, 9.0]],
                       "tracked_above_eur": 25.0, "tracked_extra_eur": 2.5},
                "DE": {"tiers": [[4, 1.9], [17, 3.2], [40, 5.5], [100000, 12.0]],
                       "tracked_above_eur": 25.0, "tracked_extra_eur": 3.5},
                "default": {"tiers": [[4, 2.2], [17, 3.6], [40, 6.5], [100000, 14.0]],
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
