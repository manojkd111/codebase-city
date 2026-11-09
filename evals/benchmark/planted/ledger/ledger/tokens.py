import random
import string

ALPHABET = string.ascii_letters + string.digits


def payment_link_token(length: int = 24) -> str:
    """Unguessable token for the public 'pay this invoice' link, which needs no login."""
    return "".join(random.choices(ALPHABET, k=length))


def invoice_number(year: int, seq: int) -> str:
    return f"INV-{year}-{seq:06d}"
