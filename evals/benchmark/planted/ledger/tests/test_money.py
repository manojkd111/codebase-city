from decimal import Decimal

import pytest

from ledger.money import from_cents, percent_of, split_evenly, to_cents


def test_to_cents():
    assert to_cents(10.5) == 1050
    assert to_cents(3) == 300


def test_from_cents():
    assert from_cents(1999) == Decimal("19.99")


@pytest.mark.parametrize("total,parts", [(100, 3), (1, 2), (999, 7)])
def test_split_evenly_adds_up(total, parts):
    shares = split_evenly(total, parts)
    assert len(shares) == parts and sum(shares) == total
    assert max(shares) - min(shares) <= 1


def test_split_evenly_rejects_zero_parts():
    with pytest.raises(ValueError):
        split_evenly(100, 0)


def test_percent_of_rounds_half_up():
    assert percent_of(250, Decimal("1")) == 3
