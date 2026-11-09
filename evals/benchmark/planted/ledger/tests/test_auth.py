import pytest

from ledger.auth import Forbidden, User, has_role, require_login, require_role


def test_has_role():
    user = User(1, None, "billing,support")
    assert has_role(user, "billing")
    assert not has_role(user, "admin")


def test_require_role_raises():
    with pytest.raises(Forbidden):
        require_role(User(1, None, "support"), "billing")


def test_require_login():
    with pytest.raises(Forbidden):
        require_login(None)
