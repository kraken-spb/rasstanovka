FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY staffing_history.py ./
COPY user_smu_access.py ./
COPY smu_api.py ./

COPY manual_employees.py ./
COPY requirements.txt ./
COPY performed_work.py ./
COPY selected_transfer.py ./
RUN pip install --no-cache-dir -r requirements.txt
COPY day_inheritance.py ./

COPY user_preferences.py outstaff_api.py app.py user_activity.py user_roles.py logs_api.py crew_api.py gdlr_api.py contractor_api.py backup_api.py location_api.py table_api.py staffing_import.py staffing_api.py staffing_export.py report_matrix.py native_pivot.py staffing_shifts.py personnel_dashboard.py attendance_status.py employee_removal.py ./
COPY tools/import_staffing.py ./tools/import_staffing.py
COPY tools/sync_gdlr_catalog.py ./tools/sync_gdlr_catalog.py
COPY tools/promote_super_admin.py ./tools/promote_super_admin.py
COPY seed.example.json ./seed.json
COPY placement_report.py placement_report_pdf.py ./
COPY position_cards.py position_cards_pdf.py ./
COPY fonts ./fonts
COPY telegram_api.py telegram_report_bot.py telegram_worker.py ./
COPY placement_verification.py ./
COPY filter_values.py report_queries.py ./
COPY templates ./templates
COPY static ./static

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/data && chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--preload", "--access-logfile", "-", "app:app"]
