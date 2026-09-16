"""Retry only fully rolled-back, reviewed database-only staffing transactions.

An HTTP stale-token response is never retried. The same submitted tokens and
payload are validated again, including fresh login, role and SMU authorization.
See PostgreSQL 17, Transaction Isolation: retry the complete transaction.
"""
from functools import wraps
import random
import time

from flask import current_app, request

from postgres_db import ConcurrentChange

RETRYABLE_ENDPOINTS = frozenset({'update_day', 'set_workplace'})
MAX_ATTEMPTS = 6


def staffing_transaction_retry(view, get_db):
    @wraps(view)
    def run(*args, **kwargs):
        if not current_app.config.get('WORKFORCE_ENABLED') or request.endpoint not in RETRYABLE_ENDPOINTS:
            return view(*args, **kwargs)
        for attempt in range(MAX_ATTEMPTS):
            try:
                return view(*args, **kwargs)
            except ConcurrentChange:
                get_db().rollback()
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                current_app.logger.info('Retry rolled-back staffing transaction: endpoint=%s attempt=%s',
                                        request.endpoint, attempt + 1)
                # Spread a burst of serialization losers across new snapshots.
                # Millisecond-only retries can repeatedly collide with the same
                # still-running transactions after a temporary server pause.
                time.sleep(random.uniform(.010, min(.320, .040 * 2 ** attempt)))
    return run
