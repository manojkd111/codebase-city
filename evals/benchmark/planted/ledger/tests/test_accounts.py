import pytest

from ledger.accounts import Account, InsufficientFunds, deposit, transfer


def test_deposit():
    a = Account(1, 0)
    deposit(a, 500)
    assert a.balance == 500


def test_deposit_rejects_non_positive():
    with pytest.raises(ValueError):
        deposit(Account(1, 0), 0)


def test_transfer_moves_money():
    a, b = Account(1, 1000), Account(2, 0)
    transfer(a, b, 400)
    assert (a.balance, b.balance) == (600, 400)


def test_transfer_insufficient_funds():
    a, b = Account(1, 100), Account(2, 0)
    with pytest.raises(InsufficientFunds):
        transfer(a, b, 101)
    assert (a.balance, b.balance) == (100, 0)
