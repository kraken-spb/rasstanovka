import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "__pycache__", "data", "output", "outputs", "backups", ".playwright-cli"}
SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".json", ".yaml", ".yml", ".txt", ".example", ".sql"}
MARKERS = {"\ufffd", "\u00d0", "\u00d1", "?" * 4}


paths = [
    path
    for path in ROOT.rglob("*")
    if path.is_file()
    and not any(part in SKIP for part in path.relative_to(ROOT).parts)
    and (path.suffix in SUFFIXES or path.name in {"Dockerfile", ".env.example"})
]
texts = {path: path.read_text(encoding="utf-8") for path in paths}
problems = [
    f"{path.relative_to(ROOT)}: {marker!r}"
    for path, content in texts.items()
    for marker in MARKERS
    if marker in content
]
if problems:
    raise SystemExit("Обнаружены маркеры повреждения:\n" + "\n".join(problems))

seed_paths = [ROOT / "seed.example.json", ROOT / "seed.json"]
for seed_path in seed_paths:
    if seed_path.exists():
        json.loads(seed_path.read_text(encoding="utf-8"))
html = [path for path in paths if path.suffix == ".html"
        and ('<html' in texts[path].lower() or '<!doctype html' in texts[path].lower())]
if not all('<meta charset="UTF-8">' in texts[path] for path in html):
    raise SystemExit("Не во всех HTML-файлах указан UTF-8.")

css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
if "@media (max-width: 900px)" not in css or "@media (max-width: 640px)" not in css:
    raise SystemExit("Не найдены контрольные точки мобильной вёрстки.")

print(
    f"Строгая проверка UTF-8: {len(paths)} файлов; "
    f"HTML meta: {len(html)}; мобильные контрольные точки: OK; seed.json: OK"
)
