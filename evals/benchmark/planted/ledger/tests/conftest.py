from types import SimpleNamespace

import pytest

from ledger.auth import User


@pytest.fixture
def customer_user():
    return User(id=1, customer_id=10, roles="")


@pytest.fixture
def make_request():
    def make(user=None, args=None, json=None):
        return SimpleNamespace(user=user, args=args or {}, json=json or {})

    return make
