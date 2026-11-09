from decimal import Decimal

from .money import percent_of

# VAT rates by country, in percent.
RATES = {
    "de": Decimal("19"),
    "fr": Decimal("20"),
    "nl": Decimal("21"),
    "ie": Decimal("23"),
}


def rate_for(region: str) -> Decimal:
    return RATES.get(region, Decimal("0"))


def tax_cents(net_cents: int, region: str) -> int:
    return percent_of(net_cents, rate_for(region))
