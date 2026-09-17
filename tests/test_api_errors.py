import unittest
from unittest.mock import patch

from flask import Flask, abort
from werkzeug.exceptions import ServiceUnavailable

from api_errors import register_api_errors


class ApiErrorTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        register_api_errors(self.app)

        @self.app.get('/api/failure')
        def failure():
            raise RuntimeError('private database details')

        @self.app.get('/api/unavailable')
        def unavailable():
            raise ServiceUnavailable(description='private database details', retry_after=10)

        @self.app.get('/api/validation')
        def validation():
            abort(409, description='Данные изменены другим пользователем.')

        self.client = self.app.test_client()

    def test_unhandled_api_failure_is_json_and_keeps_server_logging(self):
        with patch.object(self.app, 'log_exception') as logger:
            response = self.client.get('/api/failure')
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertIn('Сервер не смог', response.json['error'])
        self.assertNotIn('private', response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        logger.assert_called_once()

    def test_service_failure_retains_status_and_retry_header(self):
        response = self.client.get('/api/unavailable')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Retry-After'], '10')
        self.assertTrue(response.is_json)
        self.assertNotIn('private', response.json['error'])

    def test_method_and_missing_route_failures_are_json(self):
        response = self.client.post('/api/validation')
        self.assertEqual(response.status_code, 405)
        self.assertTrue(response.is_json)
        self.assertIn('GET', response.headers['Allow'])
        missing = self.client.get('/api/unknown')
        self.assertEqual(missing.status_code, 404)
        self.assertTrue(missing.is_json)

    def test_validation_message_and_status_are_preserved(self):
        response = self.client.get('/api/validation')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'Данные изменены другим пользователем.')

    def test_page_error_is_still_html(self):
        response = self.client.get('/missing-page')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, 'text/html')


if __name__ == '__main__':
    unittest.main()
