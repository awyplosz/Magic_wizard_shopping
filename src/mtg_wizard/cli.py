"""Command line entry point: `mtg-wizard deck.txt` (or `python -m mtg_wizard deck.txt`)."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
from pathlib import Path

from .config import load_config
from .currency import usd_to_eur
from .decklist import load_decklist
from .http import HttpClient
from .optimizer import best_single_seller, cheapest_per_card, optimize
from .providers import PROVIDERS, Context, offer_allowed
from .report import plan_to_text, write_csv, write_markdown, write_marketplace_lists
from .scryfall import ScryfallResolver

log = logging.getLogger("mtg_wizard")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mtg-wizard",
        description="Cheapest way to buy a decklist across Cardmarket, TCGplayer, Card Kingdom and "
                    "Paris shops (Play-in, Parkage, Troll2Jeux), shipping included.")
    p.add_argument("decklist", help="decklist text file (MTGA/Moxfield/plain '4 Card Name' format)")
    p.add_argument("-c", "--config", help="YAML config file (default: ./config.yaml if present)")
    p.add_argument("-o", "--out", default="output", help="output folder (default: output/)")
    p.add_argument("--providers", help=f"comma-separated subset of: {', '.join(PROVIDERS)}")
    p.add_argument("--exclude", help="comma-separated providers to skip")
    p.add_argument("--max-sellers", type=int, help="maximum number of sellers/shops in the cart")
    p.add_argument("--time-limit", type=int, help="solver time limit in seconds")
    p.add_argument("--languages", help="accepted languages, e.g. en,fr")
    p.add_argument("--min-condition", help="MT, NM, EX, GD, LP, PL or PO")
    p.add_argument("--no-foil", action="store_true", help="never buy foils")
    p.add_argument("--no-estimates", action="store_true",
                   help="ignore Cardmarket price-guide estimates (only real listings)")
    p.add_argument("--skip-basic-lands", action="store_true", help="ignore basic lands in the decklist")
    p.add_argument("--main-only", action="store_true", help="ignore the sideboard")
    p.add_argument("--offline", action="store_true", help="use cached data only, no network")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def apply_overrides(cfg: dict, args) -> dict:
    prefs = cfg["preferences"]
    if args.languages:
        prefs["languages"] = [x.strip() for x in args.languages.split(",") if x.strip()]
    if args.min_condition:
        prefs["min_condition"] = args.min_condition.upper()
    if args.no_foil:
        prefs["allow_foil"] = False
    if args.max_sellers:
        cfg["solver"]["max_sellers"] = args.max_sellers
    if args.time_limit:
        cfg["solver"]["time_limit_s"] = args.time_limit
    if args.no_estimates:
        cfg["solver"]["include_estimates"] = False
    names = list(PROVIDERS)
    if args.providers:
        wanted = {x.strip() for x in args.providers.split(",")}
        for n in names:
            cfg["providers"].setdefault(n, {})["enabled"] = n in wanted
    if args.exclude:
        for n in args.exclude.split(","):
            cfg["providers"].setdefault(n.strip(), {})["enabled"] = False
    return cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = apply_overrides(load_config(args.config), args)

    sections = ("main", "commander", "companion") if args.main_only else ("main", "side", "commander", "companion")
    deck = load_decklist(args.decklist, include_sections=sections, skip_basic_lands=args.skip_basic_lands)
    if not deck:
        print("No card found in the decklist.")
        return 1
    print(f"Decklist: {sum(r.quantity for r in deck)} cards ({len(deck)} different)")

    http = HttpClient(cfg["http"]["cache_dir"], cfg["http"]["default_delay_s"], offline=args.offline)
    resolver = ScryfallResolver(http)
    t0 = time.time()
    cards, unknown = resolver.resolve(
        deck, progress=lambda i, n, name: print(f"\r  Scryfall {i}/{n}: {name[:40]:<40}", end="", flush=True))
    print(f"\rResolved {len(cards)} cards with Scryfall in {time.time() - t0:.0f}s" + " " * 30)
    for u in unknown:
        print(f"  ! unknown card name: {u.name}")

    ctx = Context(cfg=cfg, http=http, usd_to_eur=usd_to_eur(http, cfg))
    results = []
    for name, cls in PROVIDERS.items():
        if not cfg["providers"].get(name, {}).get("enabled", False):
            continue
        provider = cls(ctx)
        t0 = time.time()
        res = provider.fetch(cards, progress=lambda lbl, i, n, c: print(
            f"\r  {lbl}: {i}/{n} {c[:35]:<35}", end="", flush=True))
        results.append(res)
        if res.disabled_reason:
            print(f"\r  {provider.label}: skipped - {res.disabled_reason}" + " " * 30)
        else:
            found = len({o.card for o in res.offers})
            print(f"\r  {provider.label}: {len(res.offers)} offers for {found}/{len(cards)} cards "
                  f"({time.time() - t0:.0f}s)" + (f", {len(res.errors)} errors" if res.errors else "") + " " * 20)
        for note in res.notes:
            print(f"      {note}")
        if args.verbose:
            for card, err in res.errors.items():
                print(f"      {card}: {err}")

    by_name = {c.name: c for c in cards}
    offers = [o for r in results for o in r.offers if offer_allowed(o, cfg, by_name.get(o.card))]
    print(f"{len(offers)} offers match your filters. Optimizing...")

    solver = cfg["solver"]
    plan = optimize(cards, offers, ctx.sellers, time_limit=int(solver["time_limit_s"]),
                    max_sellers=solver.get("max_sellers"), missing_penalty=float(solver["missing_card_penalty_eur"]),
                    max_offers_per_card=int(solver["max_offers_per_card"]))
    baselines = [cheapest_per_card(cards, offers, ctx.sellers)]
    single = best_single_seller(cards, offers, ctx.sellers)
    if single:
        baselines.append(single)

    print()
    print(plan_to_text(plan))
    print("\nFor comparison:")
    for b in baselines:
        print(f"  - {b.name}: {len(b.parcels)} sellers, {b.total:.2f} EUR"
              + (f", {sum(b.missing.values())} cards missing" if b.missing else ""))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.decklist).stem
    md, csv_path, offers_path = out / f"{stem}_shopping.md", out / f"{stem}_shopping.csv", out / f"{stem}_offers.json"
    write_markdown(md, plan, baselines, results, [u.name for u in unknown])
    write_csv(csv_path, plan)
    offers_path.write_text(json.dumps([{**dataclasses.asdict(o), "condition": o.condition.name if o.condition else None}
                                       for o in offers], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSaved: {md}\n       {csv_path}\n       {offers_path}")
    lists = write_marketplace_lists(out, stem, plan, cards)
    if lists:
        print("\nCardmarket / TCGplayer (cards not bought in a Paris shop):")
        for p in lists:
            print(f"       {p}")
        print("  -> paste the wants file into Cardmarket 'Ajouter une deck list' and run the Shopping Wizard,")
        print("     or save product pages (Ctrl+S) into cardmarket_pages/ and run again. See README.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
