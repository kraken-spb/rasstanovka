import unittest
import test_staffing as staffing_tests
from test_staffing import workbook_bytes
from staffing_import import parse_attendance


class ManualStaffingPeopleTests(unittest.TestCase):
    setUpClass = classmethod(staffing_tests.StaffingWorkflowTest.setUpClass.__func__)
    tearDownClass = classmethod(staffing_tests.StaffingWorkflowTest.tearDownClass.__func__)
    setUp = staffing_tests.StaffingWorkflowTest.setUp
    client = staffing_tests.StaffingWorkflowTest.client
    write = staffing_tests.StaffingWorkflowTest.write
    table = staffing_tests.StaffingWorkflowTest.table
    apply = staffing_tests.StaffingWorkflowTest.apply

    def test_manual_person_survives_import_and_can_be_selected(self):
        first = self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            person_id = db.execute('''INSERT INTO staffing_people
                (import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew)
                VALUES (?,-1,'Ручной Андрей Валентинович','56203','Прораб','','','')''', (first['id'],)).lastrowid
            db.execute('INSERT INTO manual_staffing_people VALUES (?,?,?)', (person_id, self.admin_id, self.module.utc_now()))
            db.commit()
        self.parsed = parse_attendance(workbook_bytes([{'number': '70004'}]), 'следующая.xlsx')
        self.apply()
        people = self.admin.get('/api/staffing/people').get_json()['people']
        manual = [p for p in people if p['personnel_no'] == '56203']
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]['source_kind'], 'manual')
        self.assertEqual(manual[0]['id'], person_id)
        self.assertFalse(any(p['personnel_no'] == '70002' for p in people))
        crew = self.table().get_json()['crews'][0]
        response = self.write(f"/api/staffing/crews/{crew['id']}/details", {
            'linear_itr': manual[0]['full_name'], 'linear_itr_person_id': person_id,
            'brigadier': crew['brigadier'], 'expected_token': crew['details_token']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.table().get_json()['crews'][0]['linear_itr_person_id'], person_id)

    def test_manual_person_does_not_prevent_file_directory_backfill(self):
        first = self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            person_id = db.execute('''INSERT INTO staffing_people
                (import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew)
                VALUES (?,-1,'Ручной сотрудник','56203','Прораб','','','')''', (first['id'],)).lastrowid
            db.execute('INSERT INTO manual_staffing_people VALUES (?,?,?)', (person_id, self.admin_id, self.module.utc_now()))
            db.execute('DELETE FROM staffing_people WHERE id=(SELECT MIN(id) FROM staffing_people WHERE source_row>0)')
            db.commit()
        result = self.apply()
        self.assertEqual(result['people_added'], 1)
        people = self.admin.get('/api/staffing/people').get_json()['people']
        self.assertEqual(len(people), 4)
