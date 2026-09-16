CREATE VIEW employee_inactive_periods AS
    SELECT worker_id, effective_date, NULL::TEXT restored_date FROM employee_removals
    UNION ALL
    SELECT worker_id, removal_json::json ->> 'effective_date', restored_date
    FROM employee_restorations WHERE removal_json IS NOT NULL;

-- Treat legacy night aliases as the same shift at the database boundary too.
CREATE UNIQUE INDEX uq_assignment_canonical_shift ON assignments
    (work_date, worker_id, (CASE WHEN shift='Ночная смена' THEN '2 смена' ELSE shift END));

GRANT SELECT ON employee_inactive_periods TO workforce_app;
