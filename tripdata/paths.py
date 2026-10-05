import gzip, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
CACHE = ROOT / "cache"
LISTS = CACHE / "lists"            # снимки выдачи trip.com по остановкам (цены на их даты)
HOTELS = CACHE / "hotels"          # анализ отзывов по отелям: cache/hotels/<город>.jsonl (сырые тексты не храним)
HOTEL_META = CACHE / "hotel_meta.json"  # последние известные данные отеля из выдачи
TRIP_DATA = CACHE / "trip_data.json"    # итог анализа (вход для экспорта)
SITE_DATA = ROOT / "site-data"     # то, что уходит в сайты: index.json, cities/, prices/

CITY_IDS = {"bkk": 359, "cm": 623, "cr": 647, "pt": 622}   # id городов на trip.com


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data, compact=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda p: gzip.open(p, "wt", encoding="utf-8", compresslevel=9)) if path.suffix == ".gz" else (lambda p: open(p, "w", encoding="utf-8"))
    with opener(path) as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 1)


def load_hotels(city):
    """id → {a: анализ тем, cr: оценки, fac: удобства, total, nt: счётчики фишек/сигналов}."""
    path = HOTELS / f"{city}.jsonl"
    out = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line); out[rec["id"]] = rec
    return out


def save_hotels(city, recs):
    path = HOTELS / f"{city}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for hid in sorted(recs):
            f.write(json.dumps(recs[hid], ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")


def list_path(stop_id):
    return LISTS / f"{stop_id}.json.gz"


def load_settings():
    return read_json(CONTENT / "settings.json")
