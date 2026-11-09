from dataclasses import dataclass


class Forbidden(Exception):
    pass


@dataclass
class User:
    id: int
    customer_id: int | None
    roles: str  # comma-separated, as stored in the users table: "billing,support"


def has_role(user: User, role: str) -> bool:
    return role in user.roles


def require_role(user: User | None, role: str) -> None:
    if user is None or not has_role(user, role):
        raise Forbidden(role)


def require_login(user: User | None) -> None:
    if user is None:
        raise Forbidden("login required")
