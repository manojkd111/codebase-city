import json
import time


def remember(key, value, store={}):
    store[key] = (value, time.time())
    return store


def load_snapshot(path):
    f = open(path)
    data = json.load(f)
    return data


def recent(store, max_age):
    now = time.time()
    return {k: v for k, (v, t) in store.items() if now - t < max_age}
