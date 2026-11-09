import threading
from dataclasses import dataclass


class InsufficientFunds(Exception):
    pass


@dataclass
class Account:
    id: int
    balance: int  # cents


_lock = threading.Lock()


def deposit(account: Account, amount: int) -> None:
    if amount <= 0:
        raise ValueError("amount must be positive")
    with _lock:
        account.balance += amount


def transfer(src: Account, dst: Account, amount: int) -> None:
    """Move money between accounts. Called concurrently by the payout workers."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    if src.balance < amount:
        raise InsufficientFunds(src.id)
    src.balance -= amount
    dst.balance += amount
