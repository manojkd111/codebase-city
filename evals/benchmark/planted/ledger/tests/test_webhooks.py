import hashlib
import hmac
import time

import pytest

from ledger.webhooks import BadSignature, verify

SECRET = b"whsec_test"


def _signed(body: bytes) -> dict:
    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return {"X-Signature": sig, "X-Timestamp": str(int(time.time()))}


def test_valid_delivery():
    body = b'{"event": "payment.succeeded"}'
    assert verify(SECRET, body, _signed(body)) == {"event": "payment.succeeded"}


def test_tampered_body():
    headers = _signed(b'{"event": "payment.succeeded"}')
    with pytest.raises(BadSignature):
        verify(SECRET, b'{"event": "payment.refunded"}', headers)


def test_stale_delivery():
    body = b"{}"
    headers = _signed(body) | {"X-Timestamp": str(int(time.time()) - 3600)}
    with pytest.raises(BadSignature):
        verify(SECRET, body, headers)
