import hashlib
import hmac
import json
import time

TOLERANCE_S = 300


class BadSignature(Exception):
    pass


def verify(secret: bytes, body: bytes, headers: dict) -> dict:
    """Check a payment-provider webhook and return its payload.

    The signature proves the delivery came from the provider; the timestamp must be recent, so an
    old delivery can't be replayed.
    """
    ts = int(headers["X-Timestamp"])
    if abs(time.time() - ts) > TOLERANCE_S:
        raise BadSignature("stale delivery")
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, headers["X-Signature"]):
        raise BadSignature("signature mismatch")
    return json.loads(body)
