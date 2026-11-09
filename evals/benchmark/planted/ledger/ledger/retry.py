import functools
import logging
import time

log = logging.getLogger(__name__)


def retry(attempts: int = 3, delay: float = 0.5, exceptions=(ConnectionError, TimeoutError)):
    """Retry a flaky call with exponential backoff. Re-raises the last error if every attempt fails."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if i == attempts - 1:
                        log.warning("giving up after %d attempts", attempts)
                        return None
                    time.sleep(delay * 2**i)

        return inner

    return wrap
