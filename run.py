"""Сборщик данных для сайтов поездки.

  python run.py prices            цены и выдача отелей на даты всех остановок (≈10 мин на город)
  python run.py reviews           докачать отзывы для новых отелей (кеш: cache/reviews)
  python run.py build             анализ → site-data/ (index.json, cities/, prices/)
  python run.py all               prices + reviews + build
  python run.py prices --stop s4  только одна остановка
  python run.py report …          подробный отчёт по кнопке на сайте (см. REPORTS.md и tripdata/report.py)
"""
import argparse, datetime, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from tripdata.paths import CITY_IDS, CONTENT, HOTEL_META, TRIP_DATA, SITE_DATA, read_json, write_json, list_path, load_settings, load_hotels, save_hotels
from tripdata import tripcom
from tripdata.build import build_trip, notes_raw, merge_notes, export_site
from tripdata.compact import compact


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def stops():
    return read_json(CONTENT / "stops.json")["stops"]


def cmd_prices(args):
    """Выдача и цены на даты каждой остановки. Три остановки параллельно."""
    meta = read_json(HOTEL_META, {}) or {}
    todo = [s for s in stops() if not args.stop or s["id"] in args.stop]
    keep = {s["anchor"] for s in stops()} | {s["proposed"] for s in stops()} | {i for v in (read_json(CONTENT / "saved.json", {}) or {}).values() for i in v}

    def work(s):
        ci, co = s["ci"].replace("-", ""), s["co"].replace("-", "")
        log(f"{s['id']} {s['title']} {s['ci']}–{s['co']}: собираю выдачу")
        return s, ci, co, tripcom.crawl_list(CITY_IDS[s["city"]], ci, co, log=lambda *a: None)

    with ThreadPoolExecutor(3) as ex:
        for s, ci, co, items in ex.map(work, todo):
            priced = sum(1 for h in items if h.get("night"))
            if len(items) < 50 or priced < 20:
                log(f"{s['id']}: подозрительно мало ({len(items)} отелей, {priced} с ценой) — trip.com мог ограничить запросы, снимок не обновляю")
                continue
            write_json(list_path(s["id"]), items)
            for it in items:  # последняя выдача нужна только для отелей из плана и сохранённых
                if it["id"] in keep: meta[str(it["id"])] = {**it, "_ci": ci, "_co": co}
            log(f"{s['id']}: {len(items)} отелей, {priced} с ценой")
    write_json(HOTEL_META, meta)


def needed():
    """Какие отели должны быть с отзывами: в лимите цены хотя бы на одну остановку, из плана, сохранённые."""
    limits = load_settings()["limits"]
    saved = read_json(CONTENT / "saved.json", {}) or {}
    need = {}
    for s in stops():
        ids = need.setdefault(s["city"], set())
        for h in read_json(list_path(s["id"]), []) or []:
            if h.get("night") and h["night"] <= limits.get(s["city"], 5000):
                ids.add(h["id"])
        ids |= {s["anchor"], s["proposed"]}
    for city, lst in saved.items():
        need.setdefault(city, set()).update(lst)
    return need


def cmd_reviews(args):
    """Скачивает отзывы новых отелей, анализирует и сохраняет только результат анализа."""
    pages = load_settings().get("maxReviewPages", 20)
    for city, ids in needed().items():
        hot = load_hotels(city)
        todo = sorted(ids) if args.refresh else sorted(i for i in ids if i not in hot)
        log(f"{city}: отзывы нужно скачать для {len(todo)} отелей")
        def work(hid):
            d = tripcom.fetch_reviews(hid, max_pages=pages)
            return hid, (compact(hid, d) if d is not None else None)
        done = 0
        with ThreadPoolExecutor(args.workers) as ex:
            for hid, rec in ex.map(work, todo):
                if rec is None: continue
                hot[hid] = rec; done += 1
                if done % 100 == 0: save_hotels(city, hot); log(f"  {done}/{len(todo)}")
        save_hotels(city, hot)
        log(f"{city}: готово {done}/{len(todo)}")


def cmd_build(args):
    log("анализ отзывов и сборка отелей по остановкам")
    D = build_trip(log=log)
    raw = notes_raw(D)
    merge_notes(D, raw)
    write_json(TRIP_DATA, D)
    idx = export_site(D, built_at=datetime.date.today().isoformat())
    log(f"site-data/ готово: {sum(c['count'] for c in idx['cities'].values())} отелей, {idx['reviews']} отзывов")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        from tripdata.report import main as report_main
        return report_main(sys.argv[2:])
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["prices", "reviews", "build", "all"])
    p.add_argument("--stop", nargs="*", help="только эти остановки (s1 … s7)")
    p.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", 8)))
    p.add_argument("--refresh", action="store_true", help="перекачать отзывы и для уже скачанных отелей")
    a = p.parse_args()
    if a.command in ("prices", "all"): cmd_prices(a)
    if a.command in ("reviews", "all"): cmd_reviews(a)
    if a.command in ("build", "all"): cmd_build(a)


if __name__ == "__main__":
    sys.exit(main())
