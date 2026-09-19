"""Explainable suggestions from existing canonical bindings, never majority voting."""
from collections import defaultdict

from workforce_core import digest, plain


def suggestions(db, rows):
    codes = sorted({row['profession_code'] for row in rows if row['profession_code']})
    professions = {r['code']: plain(r) for r in db.native('''SELECT c.code,c.label,c.active,c.edit_token,
        s.active parent_active,s.edit_token parent_token FROM workforce_catalog c
        LEFT JOIN workforce_catalog s ON s.code=c.specialty_code
        WHERE c.code=ANY(%s) AND c.kind='profession' ORDER BY c.code''', (codes,))}
    evidence = defaultdict(list)
    # Include inactive categories in the evidence: excluding them could conceal a conflict.
    for row in db.native('''SELECT w.profession_code,g.id,g.name,g.active,g.edit_token,count(*) employee_count
        FROM workers w JOIN employee_gdlr e ON e.worker_id=w.id
        JOIN gdlr_categories g ON g.id=e.category_id
        WHERE w.active=1 AND w.profession_code=ANY(%s)
        GROUP BY w.profession_code,g.id,g.name,g.active,g.edit_token ORDER BY w.profession_code,g.id''', (codes,)):
        evidence[row['profession_code']].append(plain(row))
    result = []
    for row in rows:
        profession = professions.get(row['profession_code'])
        candidates = evidence.get(row['profession_code'], [])
        item = {'id': row['id'], 'name': row['full_name'], 'profession': row['profession'],
                'current': row['category'], 'category_id': None, 'category': None,
                'evidence_token': digest([profession, candidates])}
        if row['category_id'] is not None or (row['category'] or '').strip():
            item.update(status='preserved', basis='Категория уже указана — сохранена')
        elif not profession or not profession['active'] or profession['parent_active'] is False:
            item.update(status='unmatched', basis='Нет действующей должности в справочнике')
        elif not candidates:
            item.update(status='unmatched', basis='По этой должности ещё нет назначений ГДЛР')
        elif len(candidates) > 1:
            item.update(status='ambiguous', basis='Несколько категорий: ' + '; '.join(c['name'] for c in candidates))
        elif not candidates[0]['active']:
            item.update(status='unmatched', basis='Найденная категория отключена в справочнике')
        else:
            category = candidates[0]
            item.update(status='matched', category_id=category['id'], category=category['name'],
                        basis=f"Единственная ГДЛР по той же должности; назначений: {category['employee_count']}")
        result.append(item)
    return result
