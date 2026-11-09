from mtg_wizard.decklist import parse_decklist, parse_line


def test_plain_and_arena_lines():
    r = parse_line("4 Lightning Bolt")
    assert (r.name, r.quantity) == ("Lightning Bolt", 4)
    r = parse_line("1x Sol Ring (C21) 263")
    assert (r.name, r.quantity, r.set_code, r.collector_number) == ("Sol Ring", 1, "c21", "263")
    r = parse_line("Anneau solaire")
    assert (r.name, r.quantity) == ("Anneau solaire", 1)
    r = parse_line("2 Fire // Ice [MH2] *F*")
    assert r.name == "Fire // Ice" and r.set_code == "mh2" and r.foil is True


def test_sections_comments_and_merge():
    text = """
    // my deck
    Deck
    4 Lightning Bolt
    2 Island
    Sideboard
    2 Lightning Bolt
    1 Pyroblast
    """
    cards = parse_decklist(text)
    by = {c.name: c.quantity for c in cards}
    assert by == {"Lightning Bolt": 6, "Island": 2, "Pyroblast": 1}
    main_only = parse_decklist(text, include_sections=("main",), skip_basic_lands=True)
    assert {c.name: c.quantity for c in main_only} == {"Lightning Bolt": 4}
