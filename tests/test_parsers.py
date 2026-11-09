import html
import json

from mtg_wizard.models import CardRequest, Condition, ResolvedCard
from mtg_wizard.providers.cardmarket import oauth_header, parse_product_page
from mtg_wizard.providers.parkage import parse_items
from mtg_wizard.providers.playin import parse_card_products
from mtg_wizard.providers.troll2jeux import parse_product, parse_search, title_matches

SOL_RING = ResolvedCard(request=CardRequest("Sol Ring", 1), name="Sol Ring", names={"fr": "Anneau solaire"})


def test_parkage_items():
    items = [
        {"id": 1, "name_en": "Sol Ring", "name_fr": "Anneau solaire", "price": 0.67, "price_discount": 0,
         "maxQuantity": 4, "state": "new", "lang": "fr", "is_foil": 0, "canSelling": True,
         "taxonomies": [{"name": "product_type_id", "values": [{"id": 9}]},
                        {"name": "set_id", "values": [{"abreviation": "NCC", "name_en": "New Capenna Commander"}]}]},
        {"id": 2, "name_en": "Sol Ring Playmat", "price": 20, "maxQuantity": 1, "taxonomies": []},
    ]
    out = parse_items(items, SOL_RING)
    assert len(out) == 1
    assert out[0]["set_code"] == "ncc" and out[0]["condition"] == Condition.NM and out[0]["qty"] == 4


def test_troll2jeux_search_and_product():
    search = """<article class="js-product-miniature"><h3><a href="https://troll2jeux.com/x/1-a.html"
      title="Anneau solaire (Sol Ring)">Anneau solaire (Sol Ring)</a></h3></article>
      <article class="js-product-miniature"><h3><a href="https://troll2jeux.com/x/2-b.html"
      title="Ange solaire (Sun Angel)">x</a></h3></article>"""
    links = [link for link in parse_search(search) if title_matches(link[0], SOL_RING)]
    assert links == [("Anneau solaire (Sol Ring)", "https://troll2jeux.com/x/1-a.html")]
    data = {"id_product": 1, "name": "Anneau solaire (Sol Ring)", "price_amount": 4, "quantity": 2,
            "reference": "MTG-LTC-U-284",
            "features": [{"name": "MTG. Edition", "value": "Commander  le Seigneur des Anneaux"}]}
    page = f'<div data-product="{html.escape(json.dumps(data))}"></div>'
    p = parse_product(page)
    assert p["qty"] == 2 and p["price"] == 4 and p["set_code"] == "ltc"


def test_playin_flight_payload():
    product = {"__typename": "CardProduct", "_id": 672256,
               "linkedEdition": {"__typename": "CardEdition", "codeName": "FRC", "transName": "Commander X"},
               "cardData": {"__typename": "CardData", "declination": {
                   "__typename": "CardDeclination", "lang": {"__typename": "Lang", "transName": "Anglais"},
                   "grading": {"__typename": "CardGrading", "name": "Mint/Nmint"}, "foil": ""}},
               "quantity": 8, "transName": "Anneau solaire", "sellPrice": 2}
    chunk = '9b:["$","div",null,{"items":[' + json.dumps(product) + ']}]\n'
    page = f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>"
    out = parse_card_products(page)
    assert out == [{"id": 672256, "name": "Anneau solaire", "qty": 8, "price": 2.0, "language": "en",
                    "condition": Condition.NM, "foil": False, "set_code": "frc", "set_name": "Commander X"}]


def test_cardmarket_saved_page():
    page = """<h1>Sol Ring<span>Commander 2021</span></h1>
    <div class="article-row">
      <span class="seller-name"><span aria-label="Item location: Germany"></span><a href="/Users/bob">bob</a></span>
      <div class="product-attributes"><a class="article-condition"><span>NM</span></a>
        <span aria-label="English"></span></div>
      <div class="price-container"><span>1,25 €</span></div>
      <div class="amount-container"><span class="item-count">3</span></div>
    </div>"""
    title, arts = parse_product_page(page)
    assert title.startswith("Sol Ring")
    assert arts == [{"seller": "bob", "country": "DE", "condition": Condition.NM, "language": "en",
                     "foil": False, "price": 1.25, "quantity": 3}]


def test_oauth_header_shape():
    h = oauth_header("GET", "https://api.cardmarket.com/ws/v2.0/output.json/articles/1", {"start": 0},
                     "app", "appsecret", "tok", "toksecret", nonce="n", timestamp="1")
    assert h.startswith('OAuth realm="https://api.cardmarket.com/ws/v2.0/output.json/articles/1"')
    assert 'oauth_signature="' in h and 'oauth_consumer_key="app"' in h
