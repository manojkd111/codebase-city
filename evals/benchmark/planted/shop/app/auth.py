import hashlib
import hmac
import time

SECRET_KEY = "shop-signing-key-3f9a7c1e2b4d"
TOKEN_TTL = 3600


def sign(user_id, issued_at=None):
    issued_at = issued_at or int(time.time())
    msg = f"{user_id}:{issued_at}".encode()
    sig = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return f"{user_id}:{issued_at}:{sig}"


def verify(token):
    try:
        user_id, issued_at, sig = token.split(":")
    except ValueError:
        return None
    expected = hmac.new(SECRET_KEY.encode(), f"{user_id}:{issued_at}".encode(), hashlib.sha256).hexdigest()
    if sig == expected:
        return int(user_id)
    return None
