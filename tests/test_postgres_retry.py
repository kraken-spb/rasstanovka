from unittest import TestCase
from unittest.mock import Mock, patch

from flask import Flask, request

from postgres_db import ConcurrentChange
from postgres_retry import MAX_ATTEMPTS, staffing_transaction_retry


class TransactionRetryTest(TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['WORKFORCE_ENABLED'] = True
        self.db = Mock()

    def call(self, view, endpoint='update_day'):
        with self.app.test_request_context('/save', method='PUT', json={'expected_tokens': {'1': 'original'}}):
            request.url_rule = Mock(endpoint=endpoint)
            with patch('postgres_retry.time.sleep'):
                return staffing_transaction_retry(view, lambda: self.db)()

    def test_replays_whole_operation_with_original_payload_after_rollback(self):
        seen = []
        def view():
            seen.append(request.get_json())
            if len(seen) == 1:
                raise ConcurrentChange()
            return 'saved'
        self.assertEqual(self.call(view), 'saved')
        self.db.rollback.assert_called_once()
        self.assertEqual(seen, [{'expected_tokens': {'1': 'original'}}] * 2)

    def test_stale_response_and_unreviewed_endpoints_are_not_retried(self):
        view = Mock(return_value=({'error': 'stale'}, 409))
        self.assertEqual(self.call(view)[1], 409)
        view.assert_called_once()
        self.db.rollback.assert_not_called()
        view = Mock(side_effect=ConcurrentChange())
        with self.assertRaises(ConcurrentChange):
            self.call(view, 'import')
        view.assert_called_once()

    def test_retries_are_bounded_and_last_failure_stays_visible(self):
        view = Mock(side_effect=ConcurrentChange())
        with self.assertRaises(ConcurrentChange):
            self.call(view)
        self.assertEqual(view.call_count, MAX_ATTEMPTS)
        self.assertEqual(self.db.rollback.call_count, MAX_ATTEMPTS)
