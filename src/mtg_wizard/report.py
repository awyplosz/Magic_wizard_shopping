"""Human-readable output: console summary, Markdown shopping list and CSV."""

from __future__ import annotations

import csv
from pathlib import Path

from .optimizer import Plan
from .providers.base import ProviderResult


def eur(v: float) -> str:
    return f"{v:,.2f} €".replace(",", " ")


def _fees_label(fees: dict[str, float], pickup: bool) -> str:
    if pickup:
        return "pick-up in store" + (f", trip {eur(fees['visit'])}" if fees["visit"] else "")
    parts = [f"postage {eur(fees['postage'])}"]
    if fees["customs"]:
        parts.append(f"import VAT/customs {eur(fees['customs'])}")
    return ", ".join(parts)


def plan_to_text(plan: Plan, detailed: bool = True) -> str:
    out = [f"=== {plan.name} [{plan.status}] ===",
           f"{plan.n_cards} cards from {len(plan.parcels)} seller(s): items {eur(plan.items_total)}"
           f" + shipping/fees {eur(plan.fees_total)} = TOTAL {eur(plan.total)}"]
    if plan.uses_estimates:
        out.append("  (!) includes Cardmarket price-guide ESTIMATES - see 'estimate' lines")
    if detailed:
        for p in plan.parcels:
            s = p.seller
            where = f"{s.provider}" + (f", {s.country}" if s.country else "")
            out.append(f"\n  {s.name} ({where}) - {p.n_cards} cards, items {eur(p.items_total)}, "
                       f"{_fees_label(p.fees, s.pickup)} -> {eur(p.total)}")
            for line in p.lines:
                o = line.offer
                tag = " [estimate]" if o.estimate else ""
                out.append(f"     {line.quantity} x {o.card:<38} {o.describe():<16} "
                           f"{eur(o.price_eur):>10}{tag}")
    if plan.missing:
        out.append("\n  NOT FOUND anywhere (with your filters):")
        for c, n in plan.missing.items():
            out.append(f"     {n} x {c}")
    return "\n".join(out)


def write_markdown(path: Path, plan: Plan, baselines: list[Plan], results: list[ProviderResult],
                   unknown_names: list[str]) -> None:
    lines = [f"# Shopping list - total {eur(plan.total)}", "",
             f"*{plan.n_cards} cards, {len(plan.parcels)} seller(s): items {eur(plan.items_total)} + "
             f"shipping/fees {eur(plan.fees_total)} ({plan.status})*", ""]
    if baselines:
        lines += ["| Strategy | Sellers | Items | Shipping/fees | Total | Missing |",
                  "|---|---:|---:|---:|---:|---:|"]
        for b in [plan, *baselines]:
            lines.append(f"| {b.name} | {len(b.parcels)} | {eur(b.items_total)} | {eur(b.fees_total)} | "
                         f"**{eur(b.total)}** | {sum(b.missing.values())} |")
        lines.append("")
    for p in plan.parcels:
        s = p.seller
        link = f"[{s.name}]({s.url})" if s.url else s.name
        lines += [f"## {link} - {eur(p.total)}",
                  f"{s.provider}{', ' + s.country if s.country else ''} - {p.n_cards} cards, items "
                  f"{eur(p.items_total)}, {_fees_label(p.fees, s.pickup)}", "",
                  "| Qty | Card | Version | Unit price | Link |", "|---:|---|---|---:|---|"]
        for line in p.lines:
            o = line.offer
            ver = o.describe() + (" *(estimate)*" if o.estimate else "")
            url = f"[open]({o.url})" if o.url else ""
            lines.append(f"| {line.quantity} | {o.card} | {ver} | {eur(o.price_eur)} | {url} |")
        lines.append("")
    if plan.missing or unknown_names:
        lines += ["## Not found", ""]
        lines += [f"- {n} x {c}" for c, n in plan.missing.items()]
        lines += [f"- {n} (name not recognised by Scryfall)" for n in unknown_names]
        lines.append("")
    lines += ["## Sources", ""]
    for r in results:
        if r.disabled_reason:
            lines.append(f"- **{r.provider}**: skipped - {r.disabled_reason}")
        else:
            lines.append(f"- **{r.provider}**: {len(r.offers)} offers"
                         + (f", {len(r.errors)} card(s) failed" if r.errors else ""))
        lines += [f"  - {n}" for n in r.notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(path: Path, plan: Plan) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["seller", "provider", "country", "quantity", "card", "set", "language", "condition",
                    "foil", "unit_price_eur", "line_total_eur", "estimate", "url"])
        for p in plan.parcels:
            for line in p.lines:
                o = line.offer
                w.writerow([p.seller.name, o.provider, p.seller.country or "", line.quantity, o.card,
                            (o.set_code or "").upper(), o.language or "", o.condition.name if o.condition else "",
                            "yes" if o.foil else "", f"{o.price_eur:.2f}", f"{line.total:.2f}",
                            "yes" if o.estimate else "", o.url or ""])
        w.writerow([])
        for p in plan.parcels:
            f = p.fees
            w.writerow([p.seller.name, p.seller.provider, p.seller.country or "", "", "SHIPPING / FEES", "", "", "",
                        "", "", f"{f['total']:.2f}", "", ""])
        w.writerow(["TOTAL", "", "", plan.n_cards, "", "", "", "", "", "", f"{plan.total:.2f}", "", ""])
