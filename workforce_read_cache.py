"""Bounded process cache keyed by a committed, transaction-visible DB revision.

Authorization is always checked before looking up a page. The database revision
changes atomically with every dependency; a cache hit therefore has the same
snapshot semantics as the surrounding read-only transaction.
"""
from collections import OrderedDict
from threading import Lock

from flask import current_app

_values = OrderedDict()
_lock = Lock()
_stripes = tuple(Lock() for _ in range(64))
_missing = object()


def cached(db, revision, key, calculate, *, latest_only=False):
    if current_app.testing:
        return calculate()
    key = (db._pool, revision, key)
    # Single flight for equal requests without serializing unrelated pages.
    with _stripes[hash(key) % len(_stripes)]:
        with _lock:
            result = _values.get(key, _missing)
            if result is not _missing:
                _values.move_to_end(key)
                return result
        result = calculate()
        with _lock:
            if latest_only:
                # Large common catalogues need only one cached revision per pool.
                # Readers of an older snapshot still calculate their exact revision.
                for previous in list(_values):
                    if previous[0] is key[0] and previous[2] == key[2] and previous != key:
                        _values.pop(previous, None)
            _values[key] = result
            _values.move_to_end(key)
            while len(_values) > 1024:
                _values.popitem(last=False)
        return result
