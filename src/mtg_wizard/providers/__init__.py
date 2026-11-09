from .base import Context, Provider, ProviderResult, ProviderUnavailable, Seller, offer_allowed
from .cardkingdom import CardKingdomProvider
from .cardmarket import CardmarketProvider
from .csv_offers import CsvProvider
from .parkage import ParkageProvider
from .playin import PlayinProvider
from .tcgplayer import TCGplayerProvider
from .troll2jeux import Troll2JeuxProvider

PROVIDERS: dict[str, type[Provider]] = {
    p.name: p for p in (
        CardmarketProvider, TCGplayerProvider, CardKingdomProvider,
        PlayinProvider, ParkageProvider, Troll2JeuxProvider, CsvProvider,
    )
}

__all__ = ["PROVIDERS", "Context", "Provider", "ProviderResult", "ProviderUnavailable", "Seller",
           "offer_allowed"]
