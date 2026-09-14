"""Bounded repeated query filters; missing filter means all values."""
from flask import abort, request


def values(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def argument(key, limit=200, ignore_empty=False, allowed=None):
    raw = request.args.getlist(key)
    if len(raw) > 100 or any(len(item) > limit for item in raw):
        abort(400, description='Слишком много значений или слишком длинный фильтр: ' + key)
    if ignore_empty:
        raw = [item for item in raw if item]
    if allowed is not None and any(item not in allowed for item in raw):
        abort(400, description='Недопустимое значение фильтра: ' + key)
    result = list(dict.fromkeys(raw))
    return None if not result else result[0] if len(result) == 1 else result


def matches(selection, value):
    return selection is None or value in values(selection)


def label(selection, all_label, empty_label):
    return all_label if selection is None else ', '.join(item or empty_label for item in values(selection))
