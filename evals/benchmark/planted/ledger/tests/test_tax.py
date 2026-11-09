from decimal import Decimal

from ledger.tax import rate_for, tax_cents


def test_rate_for_known_region():
    assert rate_for("de") == Decimal("19")


def test_tax_cents():
    assert tax_cents(10000, "fr") == 2000


def test_unknown_region_is_untaxed():
    assert tax_cents(10000, "us") == 0
