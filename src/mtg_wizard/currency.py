"""USD -> EUR conversion (ECB rate via frankfurter.app, cached; config fallback)."""

from __future__ import annotations

import logging

from .http import HttpClient

log = logging.getLogger(__name__)


def usd_to_eur(http: HttpClient, cfg: dict) -> float:
    fixed = cfg.get("currency", {}).get("usd_to_eur")
    if fixed:
        return float(fixed)
    try:
        data = http.get_json_cached("fx", "https://api.frankfurter.app/latest", 12,
                                    params={"from": "USD", "to": "EUR"})
        return float(data["rates"]["EUR"])
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback rate
        fb = float(cfg.get("currency", {}).get("fallback_usd_to_eur", 0.86))
        log.warning("Could not fetch USD/EUR rate (%s); using %.3f", exc, fb)
        return fb
