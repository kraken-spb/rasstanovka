"""Keep personnel conflicts explicit without exposing other employees' data."""
import sqlite3
import unittest
from unittest.mock import MagicMock

from flask import Flask, g
import psycopg

from workforce_api import register_workforce_routes


class PersonnelConflictResponseTest(unittest.TestCase):
    def response(self, cause, path='people/create-options'):
        error = sqlite3.IntegrityError('private database details')
        error.__cause__ = cause
        db = MagicMock(dialect='postgres')
        db.native.side_effect = error
        app = Flask(__name__)
        app.config['TESTING'] = True

        @app.before_request
        def actor():
            g.user = {'id': 1, 'role': 'admin'}

        register_workforce_routes(app, lambda: db, lambda *roles: lambda fn: fn)
        response = app.test_client().get('/api/workforce/' + path)
        db.rollback.assert_called_once()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('private', response.get_data(as_text=True))
        return response.json['error']

    def test_personnel_unique_constraint_is_reported_in_both_sections(self):
        for section in ('rotation', 'recruitment'):
            with self.subTest(section=section):
                cause = psycopg.errors.UniqueViolation(info={110: b'uq_workers_0'})
                message = self.response(cause, 'people?section=' + section)
                self.assertIn('Табельный номер уже занят', message)
                self.assertIn('не сохранены', message)

    def test_other_integrity_errors_keep_their_existing_response(self):
        for cause in (psycopg.errors.UniqueViolation(info={110: b'other_key'}),
                      psycopg.errors.ForeignKeyViolation(), None):
            self.assertNotIn('Табельный номер', self.response(cause))
