import os
import unittest
from tests import test_postgres_catalogs as catalogs


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select isolated PostgreSQL explicitly.')
class PostgresPpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalogs.PostgresCatalogTest.setUpClass()

    def test_pps_crud_and_smu_foreign_key(self):
        fixture = catalogs.PostgresCatalogTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from pps_api import register_pps_routes
        register_pps_routes(fixture.app, lambda: fixture.db, lambda *roles: lambda fn: fn, lambda: '2026-09-18')
        client = fixture.client
        name = 'ППС ' + fixture.fixture.suffix
        response = client.post('/api/pps', json={'name': name})
        self.assertEqual(response.status_code, 201, response.json)
        pps_id = response.json['id']
        response = client.post('/api/smu', json={'name': name+' участок', 'pps_id': pps_id})
        self.assertEqual(response.status_code, 201, response.json)
        smu_id = response.json['id']
        row = next(r for r in client.get('/api/pps').json['rows'] if r['id'] == pps_id)
        self.assertEqual(row['smu'][0]['id'], smu_id)
        self.assertEqual(client.delete('/api/pps/'+str(pps_id), json={'expected_token': row['edit_token']}).status_code, 409)
        smu = next(r for r in client.get('/api/smu').json['rows'] if r['id'] == smu_id)
        response = client.patch('/api/smu/'+str(smu_id), json={'name': smu['name'], 'active': True,
            'pps_id': None, 'expected_token': smu['edit_token']})
        self.assertEqual(response.status_code, 200, response.json)
        response = client.patch('/api/pps/'+str(pps_id), json={'name': name+' новый', 'active': False,
            'expected_token': row['edit_token']})
        self.assertEqual(response.status_code, 200, response.json)
        current = next(r for r in client.get('/api/pps').json['rows'] if r['id'] == pps_id)
        self.assertEqual(client.delete('/api/pps/'+str(pps_id), json={'expected_token': current['edit_token']}).status_code, 200)
