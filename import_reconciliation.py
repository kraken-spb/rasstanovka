"""Read-only attendance comparison and explicit identity decisions."""
import hashlib
import json
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from staffing_import import ImportProblem


def name_key(value):
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().replace('ё', 'е').split())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def reconcile(db, parsed, decisions=None):
    decisions = decisions or {}
    if not isinstance(decisions, dict) or len(decisions) > 50000 or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in decisions.items()):
        raise ImportProblem('Некорректные решения сверки. Проверьте файл заново.')
    label = parsed.get('source_label', '')
    workers = [dict(r) for r in db.execute('''SELECT w.*,m.crew_id,c.name crew_name,c.import_key,
        me.worker_id manual,er.worker_id removed,om.worker_id outstaff,ec.category_id,ec.edit_token category_token,
        COALESCE(gc.name,w.category) effective_category
        FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
        LEFT JOIN crews c ON c.id=m.crew_id LEFT JOIN manual_employees me ON me.worker_id=w.id
        LEFT JOIN employee_removals er ON er.worker_id=w.id
        LEFT JOIN outstaff_members om ON om.worker_id=w.id
        LEFT JOIN employee_gdlr ec ON ec.worker_id=w.id
        LEFT JOIN gdlr_categories gc ON gc.id=ec.category_id ORDER BY w.id''')]
    by_number = {w['personnel_no'].casefold(): w for w in workers}
    by_id = {w['id']: w for w in workers}
    latest = [dict(r) for r in db.execute('''SELECT id,source_label,sha256 FROM staffing_imports
        WHERE id IN (SELECT MAX(id) FROM staffing_imports GROUP BY source_label) ORDER BY id''')]
    previous = next((r['id'] for r in latest if r['source_label'] == label), None)
    previous_ids = {r[0] for r in db.execute('SELECT worker_id FROM staffing_import_members WHERE import_id=?', (previous,))}
    already = db.execute('SELECT id,source_label FROM staffing_imports WHERE sha256=?', (parsed['sha256'],)).fetchone()
    issues = list(parsed['issues'])
    if already and already['source_label'] != label:
        issues.append('Этот файл уже загружен с другой отметкой ППС.')
    manual_index = defaultdict(list)
    for w in workers:
        if w['manual'] and w['active'] and not w['removed'] and not w['outstaff'] and w['pps'] in ('', label):
            nk = name_key(w['full_name'])
            for part in {'initial:' + nk[:1], *nk.split()}:
                manual_index[part].append(w)
    added, missing, matched, reviews, skipped, actions = [], [], [], [], [], []
    selected_ids, used_keys = set(), set()

    def card(w):
        return {k: w.get(k) for k in ('id', 'full_name', 'personnel_no', 'department', 'crew_name', 'effective_category', 'manual')}

    for row in parsed['rows']:
        key = str(row['source_row'])
        old = by_number.get(row['personnel_no'].casefold())
        action = {'source_row': row['source_row'], 'worker_id': old['id'] if old else None, 'update_name': False}
        if old and old['removed']:
            skipped.append(card(old))
            continue
        review = None
        if old and name_key(old['full_name']) != name_key(row['full_name']):
            review = {'key': key, 'kind': 'name', 'file': row, 'candidates': [card(old)],
                      'options': [{'value': 'keep', 'label': 'Сохранить ФИО из системы'},
                                  {'value': 'excel', 'label': 'Подтвердить ФИО из Excel'}]}
        elif not old:
            candidates = []
            nk = name_key(row['full_name'])
            pool = {w['id']: w for part in {'initial:' + nk[:1], *nk.split()} for w in manual_index[part]}
            for w in pool.values():
                wk = name_key(w['full_name'])
                score = SequenceMatcher(None, nk, wk).ratio()
                if sorted(nk.split()) == sorted(wk.split()):
                    score = 1
                if score >= .82:
                    candidates.append((score, w))
            candidates.sort(key=lambda pair: (-pair[0], pair[1]['id']))
            if candidates:
                review = {'key': key, 'kind': 'manual', 'file': row,
                          'candidates': [card(w) for _, w in candidates[:8]],
                          'options': [{'value': 'new', 'label': 'Это новый сотрудник — создать отдельно'}] + [
                              {'value': 'match:' + str(w['id']), 'label': 'Связать: ' + w['full_name'] + ' · ' + w['personnel_no']}
                              for _, w in candidates[:8]]}
        if review:
            used_keys.add(key)
            decision = decisions.get(key, '')
            if decision and decision not in {o['value'] for o in review['options']}:
                raise ImportProblem('Выбранное совпадение недоступно. Проверьте файл заново.')
            review['decision'] = decision
            reviews.append(review)
            if decision == 'excel':
                action['update_name'] = True
            elif decision.startswith('match:'):
                old = by_id[int(decision.split(':')[1])]
                action.update(worker_id=old['id'], update_name=True)
            elif not decision:
                continue
        if old:
            if old['id'] in selected_ids:
                issues.append('Несколько строк Excel связаны с одним сотрудником: ' + old['full_name'] + '.')
            selected_ids.add(old['id'])
            if not old['active']:
                issues.append(f"Строка {row['source_row']}: сотрудник отключён. Нужна ручная проверка.")
            if old['pps'] and old['pps'] != label:
                issues.append(f"Строка {row['source_row']}: сотрудник уже отмечен как {old['pps']}.")
            if old['outstaff']:
                issues.append(f"Строка {row['source_row']}: табельный номер относится к аутстаффу. Нужна отдельная проверка.")
            matched.append({'system': card(old), 'file': row, 'name_will_change': action['update_name'],
                            'number_will_change': old['personnel_no'] != row['personnel_no'],
                            'category_preserved': old['effective_category']})
            if old['id'] not in previous_ids and not old['manual']:
                added.append({**row, 'existing_worker_id': old['id']})
        else:
            added.append(row)
        actions.append(action)
    if set(decisions) - used_keys:
        raise ImportProblem('Состав сверки изменился. Очистите решения и проверьте файл заново.')
    for worker_id in sorted(previous_ids - selected_ids):
        w = by_id.get(worker_id)
        if w and w['active'] and not w['removed'] and not w['manual']:
            missing.append(card(w))
    manual_kept = [card(w) for w in workers if w['manual'] and w['active'] and not w['removed']
                   and w['pps'] in ('', label) and w['id'] not in selected_ids]
    # Include directory changes in the snapshot: another import can extend it without changing workers.
    people_state = [dict(r) for r in db.execute('SELECT * FROM staffing_people ORDER BY id')]
    fingerprint = digest([parsed['sha256'], label, decisions, workers, latest, sorted(previous_ids), people_state])
    if already and already['source_label'] == label:
        # Re-uploading an old workbook must never restore an obsolete roster.
        added, missing = [], []
    return {'fingerprint': fingerprint, 'repeat': previous is not None, 'already_imported': bool(already),
            'added': added, 'missing': missing, 'matched': matched, 'manual_kept': manual_kept,
            'skipped': skipped, 'reviews': reviews, 'issues': issues, 'actions': actions,
            'unresolved': sum(not r['decision'] for r in reviews),
            'counts': {'added': len(added), 'missing': len(missing), 'matched': len(matched),
                       'manual_kept': len(manual_kept), 'skipped': len(skipped)}}


def check_review(db, parsed, decisions, expected):
    plan = reconcile(db, parsed, decisions)
    if plan['fingerprint'] != expected:
        raise ImportProblem('Состав или данные сотрудников изменились после проверки. Проверьте файл заново.')
    if plan['issues'] or plan['unresolved']:
        raise ImportProblem('Сначала разрешите все вопросы сверки и повторно проверьте файл.')
    return plan
