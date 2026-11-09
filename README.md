# Magic Wizard Shopping

Give it a Magic: The Gathering decklist; it returns the **cheapest complete shopping list**
across online marketplaces and Paris game shops, **shipping, import VAT and customs fees
included**.

It addresses the main weakness of Cardmarket's Shopping Wizard: you get either one seller with
expensive cards, or 40 sellers with cheap cards and shipping that costs 10× the cards. This tool
solves the real trade-off exactly, with a mixed-integer linear program (MILP).

| Source | How prices are obtained | Shipping model |
|---|---|---|
| **Play-in** (Magic Bazar redirects there) | card pages (server-rendered), ids from the public sitemap | pick-up in Paris (0 €) |
| **Parkage** | public JSON search API | pick-up in Paris (0 €) |
| **Troll2Jeux** | search + product pages | pick-up in Paris (0 €) |
| **Card Kingdom** *(off by default: shipping to the EU paused since Nov 2026)* | public daily price list (real stock per printing & condition) | international parcel + 25 % VAT + 3 € customs fee |
| **Cardmarket** | pages you saved from your browser, or public price guide *(estimate)*; Wants-list export for the Shopping Wizard | per seller country, by number of cards, tracked above 25 € |
| **TCGplayer** | TCGplayer market price via Scryfall *(estimate)*; Mass Entry export | international parcel + import VAT |
| **Any other shop** | your own CSV file | your value (0 = pick-up) |

A shop that doesn't have a card, or a site that is down, never blocks the run. The card is
bought elsewhere, or listed as *not found*.

## Install

Requires Python ≥ 3.10.

```bash
git clone https://github.com/awyplosz/Magic_wizard_shopping.git Magic_shopping_wizard
cd Magic_shopping_wizard
python -m venv %USERPROFILE%\magic_wizard      # environment outside OneDrive
%USERPROFILE%\magic_wizard\Scripts\activate
pip install -e ".[dev]"
```

## Use

```bash
mtg-wizard decks\example.txt
```

(with the environment activated; otherwise call `%USERPROFILE%\magic_wizard\Scripts\mtg-wizard`)

The decklist accepts plain `4 Lightning Bolt`, `4x ...`, MTG Arena/Moxfield exports
(`1 Sol Ring (C21) 263`, `*F*` for foil), `Deck`/`Sideboard`/`Commander` sections, and
**French or English card names** (resolved with Scryfall).

Useful options:

```text
--max-sellers 5          at most 5 parcels / shops
--languages en,fr        accepted languages            --min-condition EX
--no-foil                --skip-basic-lands            --main-only
--providers playin,parkage,troll2jeux    only these sources
--exclude tcgplayer      --no-estimates (real listings only)
--offline                reuse cached data (.cache/)   -c my_config.yaml
```

Output (in `output/`):

* the shopping list grouped by seller/shop in the console, and in `<deck>_shopping.md` with links,
  plus a comparison with two naive strategies ("cheapest copy of each card" and "everything
  from one seller");
* `<deck>_shopping.csv` (opens in Excel);
* `<deck>_offers.json`: every offer that was considered;
* for the cards **not** bought in a Paris shop: `<deck>_cardmarket_wants.txt`,
  `<deck>_tcgplayer_mass_entry.txt` and `<deck>_marketplace_links.html` (see below).

## Recommended workflow (Cardmarket and TCGplayer)

Cardmarket and TCGplayer block automated access, and neither offers an API you can sign up for. Real seller offers from those two sites therefore have to come through **your own
browser**. The tool keeps that manual part as small as possible:

1. Run `mtg-wizard deck.txt`. It finds what the Paris shops can supply cheaply and which cards
   are left for online purchase.
2. **Cardmarket Shopping Wizard:** on Cardmarket, open *Wants* → *Ajouter une deck list*, paste
   `output/<deck>_cardmarket_wants.txt` and run the Shopping Wizard on that smaller list. The
   Paris shops already cover the rest, so you are no longer paying shipping on cards you could
   pick up in store.
3. **Or fully optimized:** open `output/<deck>_marketplace_links.html`, click each card's
   Cardmarket link, set your filters, and save the page with `Ctrl+S` (*web page, HTML only*)
   into `cardmarket_pages/`. Run `mtg-wizard` again: those real offers, with real sellers and
   countries, now go into the optimization next to the Paris shops and Card Kingdom.
4. **TCGplayer:** paste `<deck>_tcgplayer_mass_entry.txt` into TCGplayer *Mass Entry* and use
   its *Cart Optimizer* (check that the sellers ship to France).

## Configuration

Copy `config.example.yaml` to `config.yaml` (git-ignored) and adjust preferences, shipping
estimates, the cost of a trip to a Paris shop (`visit_cost_eur`, 0 by default), and so on.
### Delivery costs to Paris

The default fees are for delivery to Paris (2026 rates):

| From | Cards | Untracked | Tracked (required above 25 €) | Source |
|---|---|---:|---:|---|
| Paris shops (Play-in, Parkage, Troll2Jeux) | any | 0 € (pick-up) | n/a | n/a |
| Cardmarket seller in France | ≤ 4 / ≤ 40 / ≤ 100 | 1.82 / 3.40 / 5.74 € | + 0.70 € | La Poste 2026 (Lettre verte / suivie) |
| Cardmarket seller in Germany | ≤ 4 / ≤ 17 / ≤ 40 / ≤ 200 | 1.55 / 2.10 / 3.60 / 3.80 € | + 3.90 € | Deutsche Post 09/2026 (Brief International, Einschreiben) |
| Cardmarket seller elsewhere | ≤ 4 / ≤ 17 / ≤ 40 | 2.20 / 3.60 / 6.50 € | + 4.00 € | rough estimate |
| TCGplayer, Card Kingdom (USA) | any | estimate | n/a | plus import VAT and 3 € customs fee |

Cardmarket prices are the national postage plus Cardmarket's packaging fee (0.30 € for a
letter, 0.50 € for a tracked letter or over 100 g). The cards per weight class come from
Cardmarket's help: 4 cards in 20 g, 17 in 50 g, 40 in 100 g. Countries other than France and
Germany, and the US imports, are estimates. Adjust them in `config.yaml` if you know better.

### Cardmarket: getting real sellers

cardmarket.com blocks automated access with a bot check, and this tool does not try to get
around it. Two sources are used, and they can be combined:

1. **Saved pages** (see the workflow above). Open a card's page on Cardmarket in your browser
   (with your filters set), press `Ctrl+S` and save it as *Web page, HTML only* into
   `cardmarket_pages/`. The offer table is parsed on the next run.
2. **Price guide** (default, automatic). This is Cardmarket's public daily price guide. It is
   used only for cards with no real Cardmarket listing, and it is shown as an *estimate*, priced
   as one extra small parcel.

### Play-in card ids

Play-in's search page cannot be automated, so card ids come from Play-in's public sitemap,
which is incomplete. Cards that are missing are listed after each run. To add one, look it up
on play-in.com, copy the number from the URL (`/fr/carte/9710/anneau-solaire`), and add it
to `config.yaml`:

```yaml
providers:
  playin:
    card_ids: {"Sol Ring": 9710}
```

### Shops without a scraper

Put prices you see in any other shop into a CSV (see `offers/example_manual.csv`) and add it
under `providers.csv.files`.

## How the optimization works

Variables: the copies bought from each offer `x`, a binary *seller used* flag `y`, the shipping
tier of each parcel, a *tracked shipping* flag and a *free-shipping reached* flag. It minimizes:

```
Σ price·x  +  Σ_sellers (postage(tier, tracked, free) + import VAT·(items + postage) + customs fee
                         + visit cost)  +  penalty · missing cards
```

subject to: every requested copy is bought or reported missing, stock limits, you can only buy
from a seller you "open", and an optional cap on the number of sellers. CBC (bundled with PuLP)
solves it to the proven optimum. Shipping tiers are modelled incrementally, with tight bounds
and only the thresholds a parcel can actually cross. On a test with 100 cards and about 3,500
offers from 400 Cardmarket sellers (at the Paris delivery rates) plus the three Paris shops, it
finds the proven optimum in about 25 s: 148.49 € (121.50 € cards + 26.99 € shipping) from 19
sellers, against 243.63 € from 77 sellers when buying the cheapest copy of each card.

Before solving, only offers that *provably* cannot help are removed:

* a seller's pricier copy of a card when its cheaper copies already cover the need;
* a copy priced above what a free pick-up shop already offers in sufficient quantity.

Neither rule applies to a seller with a free-shipping threshold, where a pricier card can be
worth buying to reach it.

### Verification

`tests/test_optimality.py` generates random problems with the real Paris delivery rates above,
card prices up to 15 € (so parcels cross the 25 € tracked threshold), limited stock, missing
cards and seller caps. A smaller synthetic set also covers free-shipping thresholds, odd tier
tables and shop visit costs. It enumerates **every possible cart**, prices
each one with the same cost function the report uses, and checks that the optimizer returns
the true cheapest cart. 250 such cases run in the test suite; sweeps of 1,000 more found no
difference.

## Project layout

```
src/mtg_wizard/
  cli.py          command line
  decklist.py     decklist parser
  scryfall.py     card names (EN/FR) -> printings, Cardmarket/TCGplayer ids
  providers/      one module per shop (base.py = common interface)
  shipping.py     shipping cost model
  optimizer.py    MILP + naive baselines
  report.py       console / Markdown / CSV output
tests/            unit tests (pytest), no network
```

Run the tests with `pytest`.

## Being a good citizen

Requests are rate-limited per site and cached on disk (`.cache/`). The tool uses only public
price files, public APIs, sitemaps and normal page loads, and it does not bypass any bot
protection. Use it for personal shopping, and respect each site's terms of use.
