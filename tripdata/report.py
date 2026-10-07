"""Подробный отчёт по отелям, отмеченным плюсом (кнопка «Подробный отчёт» на сайте).

Как это работает
  1. Сайт кладёт заявку в Firestore: trips/<TRIP>/reports/<id> =
     {stop, hotels: [id…], name, t, status: "queued"}.
  2. Запланированная задача Claude дежурит днём: `python run.py report wait` каждые 30 секунд
     смотрит очередь и возвращается, как только появилась заявка (сам скрипт — без Claude, бесплатно).
  3. По заявке (весь порядок — в REPORTS.md):
       report take <id>     забрать заявку (status: working), чтобы её не взял второй обработчик
       report fetch <id>    скачать ВСЕ отзывы и карточку каждого отеля в work/<id>/
       — Claude размечает отзывы по темам: work/<id>/<отель>/tags/NN.txt и notes/NN.json
       report tally <id>    точные цифры по темам: stats.json и общий notes.json у каждого отеля
       — Claude пишет текст: work/<id>/report.json
       report put <id>      проверить, дополнить цифрами и отправить на сайт (status: ready)
     report status <id> "текст" — что сейчас происходит (видно в окне на сайте),
     report fail <id> "причина" — отказ с понятной причиной.
Тексты отзывов в репозиторий не попадают: work/ в .gitignore.
"""
import datetime, json, math, os, re, sys, time
from pathlib import Path
import requests
from . import tripcom
from .paths import ROOT, SITE_DATA, list_path, read_json

PROJECT = "thailand-trip-2026-66d21"
API_KEY = "AIzaSyDg5v-r3C3AHMGWoEXmGxHswpuDbpDHotw"   # публичный ключ веб-приложения, доступ ограничен правилами базы
TRIP = "thai2026-6hvxyynirl"
DOCS = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
COL = f"trips/{TRIP}/reports"
WORK = ROOT / "work"

STALE_MS = 3 * 3600 * 1000      # «working» дольше трёх часов — обработчик, видимо, упал: можно забрать заново
CHUNK_CHARS = 38000             # часть отзывов для одного прохода разметки
CHUNK_MAX = 200
MAX_PAGES = 150                 # 50 отзывов на страницу: до 7500 отзывов на отель
DUTY_MIN = 60                   # сколько минут один запуск дежурит у очереди
WAIT_STEP = 30                  # как часто смотреть очередь, секунд
WAIT_CALL_MIN = 9               # сколько ждать за один вызов `report wait` (Bash держит команду до 10 минут)

# Темы, по которым размечаются отзывы. Ключи — в tags/*.txt и report.json, подписи — на сайте.
CATS = [
    ("loc", "Расположение и окрестности", "до чего близко, транспорт, еда и магазины рядом, ходить пешком"),
    ("clean", "Чистота", "номер, бельё, ванная, уборка"),
    ("room", "Номер", "размер, состояние, ремонт, мебель, планировка"),
    ("bed", "Кровать и сон", "матрас, подушки, бельё, удобно ли спать"),
    ("bath", "Ванная и вода", "душ, напор, горячая вода, слив, туалет"),
    ("ac", "Кондиционер и климат", "холодно/жарко, шумит ли кондиционер"),
    ("noise", "Шум и звукоизоляция", "улица, соседи, бары, стройка, тонкие стены"),
    ("staff", "Персонал и сервис", "ресепшн, дружелюбие, помощь, английский"),
    ("checkin", "Заселение, выезд, бронь", "ранний заезд, поздний выезд, депозит, проблемы с бронью"),
    ("breakfast", "Завтрак", "есть ли, выбор, вкус, часы, цена"),
    ("food", "Ресторан, бар, еда в отеле", "кроме завтрака: ресторан, бар, рум-сервис"),
    ("pool", "Бассейн", "размер, чистота, часы, вода, лежаки"),
    ("gym", "Спортзал, спа, массаж", ""),
    ("view", "Вид и окна", "вид из окна, номера без окон"),
    ("wifi", "Интернет", ""),
    ("amen", "Удобства в номере", "холодильник, чайник, ТВ, сейф, косметика, полотенца, розетки"),
    ("common", "Общие зоны и территория", "лобби, лифт, сад, коворкинг, прачечная, крыша"),
    ("value", "Цена и соотношение цена/качество", ""),
    ("insects", "Насекомые", "тараканы, клопы, муравьи, комары"),
    ("smell", "Запахи, сырость, плесень", ""),
    ("safety", "Безопасность", "сейф, охрана, район ночью, кражи"),
    ("transport", "Трансфер, такси, парковка, прокат", ""),
    ("family", "Дети и семья", ""),
    ("other", "Другое", "важное, что не попало в темы выше"),
]
CAT_KEYS = [c[0] for c in CATS]
CAT_TITLE = {c[0]: c[1] for c in CATS}


def log(*a):
    print(*a, flush=True)


def now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------- Firestore REST

def _enc(v):
    if v is None: return {"nullValue": None}
    if isinstance(v, bool): return {"booleanValue": v}
    if isinstance(v, int): return {"integerValue": str(v)}
    if isinstance(v, float): return {"doubleValue": v}
    if isinstance(v, str): return {"stringValue": v}
    if isinstance(v, (list, tuple)): return {"arrayValue": {"values": [_enc(x) for x in v]}}
    if isinstance(v, dict): return {"mapValue": {"fields": {k: _enc(x) for k, x in v.items()}}}
    raise TypeError(type(v))


def _dec(v):
    if "nullValue" in v: return None
    if "booleanValue" in v: return v["booleanValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return v["doubleValue"]
    if "stringValue" in v: return v["stringValue"]
    if "timestampValue" in v: return v["timestampValue"]
    if "arrayValue" in v: return [_dec(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: _dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def _doc(d):
    out = {k: _dec(v) for k, v in (d.get("fields") or {}).items()}
    out["_id"] = d["name"].rsplit("/", 1)[-1]
    out["_ut"] = d.get("updateTime")
    return out


def _err(r):
    try:
        return r.json()["error"]["message"]
    except Exception:
        return r.text[:300]


# REPORT_DRY=1 — проба без базы: заявка и её поля живут в work/<id>/_doc.json
DRY = bool(os.environ.get("REPORT_DRY"))


def _dry_path(rid):
    return WORK / rid / "_doc.json"


def fs_get(rid):
    if DRY:
        p = _dry_path(rid)
        return {**json.loads(p.read_text(encoding="utf-8")), "_id": rid, "_ut": "dry"} if p.exists() else None
    r = requests.get(f"{DOCS}/{COL}/{rid}", params={"key": API_KEY}, timeout=30)
    if r.status_code == 404: return None
    if not r.ok: raise RuntimeError(f"Firestore: {r.status_code} {_err(r)}")
    return _doc(r.json())


def fs_patch(rid, fields, update_time=None):
    """Меняет только перечисленные поля. update_time — «только если документ не менялся с тех пор»."""
    if DRY:
        p = _dry_path(rid)
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d.update(fields); p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        log(f"  [проба] {rid}: " + ", ".join(f"{k}={str(v)[:60]}" for k, v in fields.items()))
        return True
    params = [("key", API_KEY)] + [("updateMask.fieldPaths", k) for k in fields]
    if update_time: params.append(("currentDocument.updateTime", update_time))
    r = requests.patch(f"{DOCS}/{COL}/{rid}", params=params, json={"fields": {k: _enc(v) for k, v in fields.items()}}, timeout=60)
    if r.status_code in (400, 409) and update_time and "FAILED_PRECONDITION" in r.text:
        return False
    if not r.ok: raise RuntimeError(f"Firestore: {r.status_code} {_err(r)}")
    return True


def fs_open():
    """Заявки, которые ждут обработки: queued и «зависшие» working."""
    body = {"structuredQuery": {
        "from": [{"collectionId": "reports"}],
        "where": {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "IN",
                                  "value": {"arrayValue": {"values": [{"stringValue": "queued"}, {"stringValue": "working"}]}}}},
        "select": {"fields": [{"fieldPath": f} for f in ("stop", "hotels", "name", "t", "status", "w")]},
    }}
    r = requests.post(f"{DOCS}/trips/{TRIP}:runQuery", params={"key": API_KEY}, json=body, timeout=30)
    if not r.ok: raise RuntimeError(f"Firestore: {r.status_code} {_err(r)}")
    out = [_doc(x["document"]) for x in r.json() if x.get("document")]
    ready = [d for d in out if d.get("status") == "queued" or now_ms() - (d.get("w") or 0) > STALE_MS]
    return sorted(ready, key=lambda d: d.get("t") or 0)


def set_status(rid, text):
    try:
        fs_patch(rid, {"prog": text[:200]})
    except Exception as e:
        log("не смог обновить статус:", e)


# ---------------------------------------------------------------- очередь

def cmd_queue():
    q = fs_open()
    if not q:
        log("ПУСТО")
        return
    for d in q:
        log(json.dumps({k: d.get(k) for k in ("_id", "stop", "hotels", "name", "status")}, ensure_ascii=False))


def cmd_wait():
    """Ждёт заявку до WAIT_CALL_MIN минут. Дежурство — DUTY_MIN минут с первого вызова в этом запуске."""
    WORK.mkdir(exist_ok=True)
    duty = WORK / ".duty"
    end = int(duty.read_text()) if duty.exists() else 0
    if not end:
        end = now_ms() + DUTY_MIN * 60000
        duty.write_text(str(end))
    stop_at = min(end, now_ms() + WAIT_CALL_MIN * 60000)
    errors = 0
    while True:
        try:
            q = fs_open(); errors = 0
        except Exception as e:
            errors += 1; q = []
            log("ошибка очереди:", e)
            if errors >= 5:
                log("ОШИБКА: очередь недоступна несколько раз подряд — заканчиваю дежурство"); return
        if q:
            d = q[0]
            log("ЗАЯВКА " + json.dumps({k: d.get(k) for k in ("_id", "stop", "hotels", "name", "status")}, ensure_ascii=False))
            return
        if now_ms() + WAIT_STEP * 1000 > stop_at:
            break
        time.sleep(WAIT_STEP)
    left = (end - now_ms()) // 60000
    log(f"ПУСТО — вызови `report wait` ещё раз (до конца дежурства {left} мин)" if left >= 1 else "ДЕЖУРСТВО ОКОНЧЕНО")


def cmd_take(rid):
    d = fs_get(rid)
    if not d:
        log("НЕТ ТАКОЙ ЗАЯВКИ"); sys.exit(3)
    if d.get("status") == "working" and now_ms() - (d.get("w") or 0) <= STALE_MS:
        log("УЖЕ В РАБОТЕ у другого обработчика"); sys.exit(3)
    if d.get("status") not in ("queued", "working"):
        log(f"ЗАЯВКА УЖЕ ЗАКРЫТА ({d.get('status')})"); sys.exit(3)
    if not fs_patch(rid, {"status": "working", "w": now_ms(), "prog": "Начинаю: скачиваю отзывы"}, update_time=d["_ut"]):
        log("ЗАЯВКУ ТОЛЬКО ЧТО ЗАБРАЛ ДРУГОЙ ОБРАБОТЧИК"); sys.exit(3)
    log(f"ВЗЯЛ {rid}: остановка {d.get('stop')}, отелей {len(d.get('hotels') or [])}, от {d.get('name')}")


def cmd_status(rid, text):
    set_status(rid, text)
    log("ок")


def cmd_fail(rid, text):
    fs_patch(rid, {"status": "error", "err": text[:500], "prog": "", "done": now_ms()})
    log("заявка закрыта с ошибкой")


# ---------------------------------------------------------------- скачивание

def _next_payload(html):
    """Данные страницы отеля из Next.js (self.__next_f.push) — словарь с hotelDetailResponse и т. п."""
    found = {}
    for s in re.findall(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)</script>", html, re.S):
        try:
            txt = json.loads(s)
        except Exception:
            continue
        if "hotelDetailResponse" not in txt and "seoHotelRooms" not in txt:
            continue
        i = txt.find(":")
        try:
            obj = json.loads(txt[i + 1:])
        except Exception:
            continue
        for key in ("hotelDetailResponse", "seoHotelRooms", "hotelCommentResponse"):
            v = _find(obj, key)
            if v is not None and key not in found: found[key] = v
    return found


def _find(o, key):
    if isinstance(o, dict):
        if key in o: return o[key]
        for v in o.values():
            x = _find(v, key)
            if x is not None: return x
    elif isinstance(o, list):
        for v in o:
            x = _find(v, key)
            if x is not None: return x
    return None


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def details_md(hid, ci, co):
    """Карточка отеля trip.com текстом: удобства по группам, правила, номера, что рядом, оценки."""
    url = f"https://ru.trip.com/hotels/detail/?hotelId={hid}&locale=ru-RU&curr=RUB&checkIn={ci}&checkOut={co}"
    html = ""
    for _ in range(3):
        try:
            html = requests.get(url, headers=tripcom.H, timeout=60).text
            if "hotelDetailResponse" in html: break
        except Exception:
            pass
        time.sleep(3)
    p = _next_payload(html)
    det = p.get("hotelDetailResponse") or {}
    L = []
    if not det:
        f = tripcom.fac(hid)
        L += ["(Подробная карточка не загрузилась — только список удобств.)", "", "## Удобства",
              *[f"- {x}" for x in f["yes"]], *[f"- НЕТ: {x}" for x in f["no"]]]
        return "\n".join(L), {}
    b = det.get("hotelBaseInfo") or {}
    star = (b.get("starInfo") or {}).get("level")
    info = {"name": (b.get("nameInfo") or {}).get("name"), "star": star, "open": b.get("openYear"), "renov": b.get("fitmentYear")}
    L.append(f"# {info['name']} (id {hid})")
    desc = det.get("hotelDescriptionInfo") or {}
    L.append(" · ".join(x for x in [f"{star}★" if star else "", *(desc.get("lables") or [])] if x))
    pos = det.get("hotelPositionInfo") or {}
    L.append(f"Адрес: {_clean(pos.get('address'))}; район: {_clean(pos.get('zoneName'))}")
    if (pos.get("trafficInfo") or {}).get("trafficDesc"): L.append(f"Транспорт: {_clean(pos['trafficInfo']['trafficDesc'])}")
    pois = (pos.get("placeInfo") or {}).get("wholePoiInfoList") or (pos.get("placeInfo") or {}).get("poiList") or []
    if pois:
        L.append("Рядом: " + "; ".join(f"{_clean(x.get('poiName') or x.get('desc'))} — {x.get('distance')}" for x in pois[:15]))
    cm = (det.get("hotelComment") or {}).get("comment") or {}
    if cm:
        sub = ", ".join(f"{x.get('showName')} {x.get('showScore')}" for x in cm.get("scoreDetail") or [])
        L.append(f"Оценка trip.com: {cm.get('score')} ({cm.get('totalComment')} отзывов){'; ' + sub if sub else ''}"
                 f"{'; ' + cm['recommend'] if cm.get('recommend') else ''}")
        info["sub"] = {x.get("showName"): x.get("showScore") for x in cm.get("scoreDetail") or []}
        info["score"] = cm.get("score")
    hl = (b.get("newHighlights") or {}).get("list") or []
    if hl:
        L += ["", "## Особенности по версии trip.com"] + [f"- {_clean(x.get('tagTitle'))}: {_clean(x.get('desc'))}" for x in hl]
    secs = [_clean(x.get("desc")) for x in desc.get("sectionList") or [] if _clean(x.get("desc"))]
    if secs:
        L += ["", "## Описание отеля"] + secs

    L += ["", "## Удобства (НЕТ — trip.com явно пишет, что этого нет; «платно/бесплатно» — как указано)"]
    fp = det.get("hotelFacilityPopV2") or {}
    for cat in fp.get("hotelFacility") or []:
        items = []
        for grp in cat.get("categoryList") or []:
            for it in grp.get("list") or []:
                name = _clean(it.get("facilityDesc"))
                if not name: continue
                extra = []
                if it.get("showTitle"): extra.append(_clean(it["showTitle"]))
                for fi in it.get("facilityInfo") or []:
                    for t in fi.get("text") or []:
                        t = _clean(t)
                        if t and t != "Нет дополнительной информации": extra.append(t)
                no = it.get("icon") == "defect" or it.get("showStyle") == 4
                items.append(("НЕТ: " if no else "") + name + (f" ({'; '.join(extra)})" if extra else ""))
        if items:
            L.append(f"### {_clean(cat.get('title'))}")
            L += [f"- {x}" for x in items]

    pol = det.get("hotelPolicyInfo") or {}
    if pol:
        L += ["", "## Правила"]
        for k, v in pol.items():
            if not isinstance(v, dict) or not v.get("title"): continue
            parts = []
            for c in v.get("content") or []:
                t = _clean((c.get("title") or "") + " " + (c.get("description") or ""))
                if t: parts.append(t)
            if v.get("cashDesc"): parts.append(_clean(v["cashDesc"]))
            if parts: L.append(f"- {_clean(v['title'])}: " + "; ".join(parts))

    rooms = (p.get("seoHotelRooms") or {}).get("physicRoomMap") or {}
    if rooms:
        L += ["", "## Номера (типы на trip.com)"]
        for r in rooms.values():
            bed = r.get("bedInfo") or {}
            parts = [(r.get("areaInfo") or {}).get("title"), "; ".join(bed.get("hover") or []) or bed.get("title"),
                     (r.get("windowInfo") or {}).get("title"), (r.get("smokeInfo") or {}).get("title"),
                     f"до {r.get('person')} взрослых" if r.get("person") else "",
                     ", ".join(x.get("title") or x.get("name") or "" for x in r.get("baseFacilityInfo") or [])]
            L.append(f"- {_clean(r.get('name'))}: " + "; ".join(_clean(x) for x in parts if _clean(x)))
    return "\n".join(L), info


def fetch_reviews(hid):
    d1 = tripcom.api(hid, 1)
    if not d1: return None, {}
    total = d1.get("totalCountForPage") or 0
    pages = min(math.ceil(total / 50), MAX_PAGES)
    cl = list((d1.get("groupList") or [{}])[0].get("commentList", []))
    for pg in range(2, pages + 1):
        d = tripcom.api(hid, pg)
        if d: cl += (d.get("groupList") or [{}])[0].get("commentList", [])
        time.sleep(0.15)
    seen, rev = set(), []
    for c in cl:
        if c.get("id") in seen: continue
        seen.add(c.get("id"))
        rev.append({"t": _clean(c.get("translatedContent") or c.get("content")), "r": c.get("rating"),
                    "d": (c.get("createDate") or "")[:10], "ci": (c.get("checkinDate") or "")[:7],
                    "lang": c.get("language"), "tt": c.get("travelTypeText") or "", "room": c.get("roomName") or ""})
    rev.sort(key=lambda x: x["d"], reverse=True)
    extra = {"total": d1.get("totalCount") or total, "pages": total,
             "tags": [[t.get("name"), t.get("commentCount"), t.get("type")] for t in d1.get("commentTagList") or []],
             "travel": [[t.get("name"), t.get("commentCount")] for t in d1.get("travelTypeList") or [] if (t.get("commentCount") or 0) > 0],
             "roomsrev": [[t.get("name"), t.get("commentCount")] for t in d1.get("roomList") or [] if (t.get("commentCount") or 0) > 0]}
    return rev, extra


def _km(a, b, c, d):
    if None in (a, b, c, d): return None
    R = 6371.0; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(x)), 2)


def _stop(stop_id):
    idx = read_json(SITE_DATA / "index.json")
    s = next((x for x in idx["stops"] if x["id"] == stop_id), None)
    if not s: raise SystemExit(f"нет остановки {stop_id}")
    city = {h["id"]: h for h in read_json(SITE_DATA / "cities" / f"{s['city']}.json", [])}
    prices = read_json(SITE_DATA / "prices" / f"{stop_id}.json", {}) or {}
    snap = {h["id"]: h for h in read_json(list_path(stop_id), []) or []}   # выдача на наши даты: метки тарифа
    return s, city, prices, snap


def cmd_fetch(rid):
    d = fs_get(rid)
    if not d: raise SystemExit("нет такой заявки")
    s, city, prices, snap = _stop(d["stop"])
    hotels = [int(h) for h in d.get("hotels") or []]
    base = WORK / rid
    base.mkdir(parents=True, exist_ok=True)
    plan = [f"Заявка {rid}: {s['title']} ({s['id']}, {s['ci']}–{s['co']}), от {d.get('name')}", ""]
    meta_all = []
    for n, hid in enumerate(hotels, 1):
        set_status(rid, f"Скачиваю отзывы и описание: отель {n} из {len(hotels)}")
        h = city.get(hid) or {}
        log(f"[{n}/{len(hotels)}] {hid} {h.get('nm', '')}: отзывы…")
        rev, extra = fetch_reviews(hid)
        if rev is None:
            log("  отзывы не загрузились — отель пропущу"); continue
        md, info = details_md(hid, s["ci"], s["co"])
        hd = base / str(hid)
        (hd / "reviews").mkdir(parents=True, exist_ok=True)
        (hd / "tags").mkdir(exist_ok=True)
        (hd / "notes").mkdir(exist_ok=True)
        withtext = [r for r in rev if len(r["t"]) > 3]
        with open(hd / "reviews.jsonl", "w", encoding="utf-8") as f:
            for i, r in enumerate(withtext, 1):
                f.write(json.dumps({"i": i, **r}, ensure_ascii=False) + "\n")
        chunks, cur, size = [], [], 0
        for i, r in enumerate(withtext, 1):
            line = f"#{i} · {r['r']}/10 · {r['d']} · {r['tt'] or '—'} · {r['room'] or '—'} · язык: {r['lang']}\n{r['t']}\n"
            if cur and (size + len(line) > CHUNK_CHARS or len(cur) >= CHUNK_MAX):
                chunks.append(cur); cur, size = [], 0
            cur.append((i, line)); size += len(line)
        if cur: chunks.append(cur)
        name = info.get("name") or h.get("nm") or str(hid)
        for k, ch in enumerate(chunks, 1):
            head = f"Отель: {name} (id {hid}) · часть {k} из {len(chunks)} · отзывы #{ch[0][0]}–#{ch[-1][0]}\n---\n"
            (hd / "reviews" / f"{k:02d}.txt").write_text(head + "\n".join(x[1] for x in ch), encoding="utf-8")
        pr = prices.get(str(hid))
        meta = {"id": hid, "name": name, "stars": info.get("star") or h.get("st"), "score": h.get("sc") or info.get("score"),
                "sub": info.get("sub") or {}, "open": info.get("open") or h.get("yr") or "", "renov": info.get("renov") or "",
                "total": extra.get("total") or len(rev), "analyzed": len(withtext), "noText": len(rev) - len(withtext),
                "night": pr[0] if pr else None, "nightsTotal": pr[1] if pr else None, "roomType": pr[3] if pr else None,
                "km": 0 if hid == s.get("anchor") else _km(s.get("alat"), s.get("alng"), h.get("la"), h.get("ln")),
                "anchor": hid == s.get("anchor"), "zone": h.get("z"), "chunks": len(chunks),
                "tcTags": extra.get("tags"), "travel": extra.get("travel"), "roomsReviewed": extra.get("roomsrev"),
                "url": f"https://ru.trip.com/hotels/detail/?hotelId={hid}&checkIn={s['ci']}&checkOut={s['co']}"}
        tags_line = "; ".join(f"{t[0]} ({t[1]}){' — минус' if t[2] == 2 else ''}" for t in extra.get("tags") or [])
        md += "\n\n## Про отзывы\n" + "\n".join(x for x in [
            f"Всего отзывов на trip.com: {meta['total']}, с текстом и доступно для чтения: {meta['analyzed']}",
            f"Метки trip.com по отзывам: {tags_line}" if tags_line else "",
            "Кто пишет: " + ", ".join(f"{a} {b}" for a, b in extra.get("travel") or []) if extra.get("travel") else "",
            "О каких номерах отзывы: " + ", ".join(f"{a} {b}" for a, b in extra.get("roomsrev") or []) if extra.get("roomsrev") else "",
            f"Цена на наши даты: {pr[0]} ₽ за ночь, {pr[1]} ₽ за всё; номер в выдаче: {pr[3]}" if pr else "Цены на наши даты нет",
            "Условия самого дешёвого тарифа в выдаче: " + ", ".join((snap.get(hid) or {}).get("tags") or []) if (snap.get(hid) or {}).get("tags") else "",
            f"До «нашего» отеля: {meta['km']} км" if meta["km"] is not None else "",
        ] if x)
        (hd / "details.md").write_text(md, encoding="utf-8")
        (hd / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        meta_all.append(meta)
        plan.append(f"- {name} (id {hid}): отзывов с текстом {len(withtext)}, частей {len(chunks)}: "
                    + ", ".join(f"{hid}/reviews/{k:02d}.txt" for k in range(1, len(chunks) + 1)))
        log(f"  отзывов {len(rev)} (с текстом {len(withtext)}), частей {len(chunks)}, карточка {'есть' if info else 'НЕТ'}")
    (base / "request.json").write_text(json.dumps({"id": rid, "stop": s["id"], "title": s["title"], "ci": s["ci"], "co": s["co"],
                                                     "name": d.get("name"), "hotels": [m["id"] for m in meta_all]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "plan.md").write_text("\n".join(plan) + "\n", encoding="utf-8")
    total = sum(m["chunks"] for m in meta_all)
    set_status(rid, f"Читаю отзывы: {sum(m['analyzed'] for m in meta_all)} шт., {len(meta_all)} отелей")
    log(f"\nГОТОВО: {base}  отелей {len(meta_all)}, частей для разметки {total}")
    log((base / "plan.md").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- подсчёт

TAG_LINE = re.compile(r"^\s*#?(\d+)\s*[:\-–—]?\s*(.*)$")
TAG = re.compile(r"([a-z]+)\s*([+\-~±])")


def _read_tags(hd):
    tags = {}
    for f in sorted((hd / "tags").glob("*.txt")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = TAG_LINE.match(line)
            if not m: continue
            got = {}
            for k, sgn in TAG.findall(m.group(2)):
                if k in CAT_TITLE: got[k] = {"+": "+", "-": "-"}.get(sgn, "~")
            tags[int(m.group(1))] = got
    return tags


def _read_notes(hd, revs):
    merged = {k: {"good": [], "bad": [], "quotes": []} for k in CAT_KEYS}
    for f in sorted((hd / "notes").glob("*.json")):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"  {f}: не читается ({e}) — переделай эту часть"); continue
        for k, v in (o or {}).items():
            if k not in merged or not isinstance(v, dict): continue
            merged[k]["good"] += [_clean(x) for x in v.get("good") or [] if _clean(x)]
            merged[k]["bad"] += [_clean(x) for x in v.get("bad") or [] if _clean(x)]
            for q in v.get("quotes") or []:
                if not isinstance(q, dict) or not _clean(q.get("t")): continue
                r = revs.get(int(q.get("i") or 0)) or {}
                merged[k]["quotes"].append({"i": q.get("i"), "t": _clean(q["t"]), "s": q.get("s") if q.get("s") in ("+", "-", "~") else "~",
                                            "r": r.get("r"), "d": r.get("d")})
    return merged


def cmd_tally(rid):
    base = WORK / rid
    req = json.loads((base / "request.json").read_text(encoding="utf-8"))
    ok = True
    for hid in req["hotels"]:
        hd = base / str(hid)
        meta = json.loads((hd / "meta.json").read_text(encoding="utf-8"))
        revs = {}
        with open(hd / "reviews.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line); revs[r["i"]] = r
        tags = _read_tags(hd)
        missing = sorted(set(revs) - set(tags))
        if missing:
            ok = False
            files = []
            for f in sorted((hd / "reviews").glob("*.txt")):
                m = re.search(r"отзывы #(\d+)–#(\d+)", f.read_text(encoding="utf-8").split("\n", 1)[0])
                if m and any(int(m.group(1)) <= i <= int(m.group(2)) for i in missing): files.append(f.name)
            log(f"{meta['name']}: без разметки {len(missing)} отзывов (#{missing[0]}…#{missing[-1]}) — части: {', '.join(files) or '?'}")
        dates = sorted(r["d"] for r in revs.values() if r.get("d"))
        last = dates[-1] if dates else ""
        cutoff = (datetime.date.fromisoformat(last) - datetime.timedelta(days=365)).isoformat() if last else ""
        recent = [r for r in revs.values() if r.get("d") and r["d"] >= cutoff]
        stats = {}
        for k in CAT_KEYS:
            ids = [i for i, t in tags.items() if k in t]
            if not ids: continue
            neg = [i for i in ids if tags[i][k] == "-"]
            rec = [i for i in ids if revs.get(i, {}).get("d", "") >= cutoff]
            stats[k] = {"n": len(ids), "pos": sum(1 for i in ids if tags[i][k] == "+"), "neg": len(neg),
                        "mix": sum(1 for i in ids if tags[i][k] == "~"),
                        "share": round(100 * len(ids) / max(1, len(revs)), 1),
                        "rn": len(rec), "rpos": sum(1 for i in rec if tags[i][k] == "+"), "rneg": sum(1 for i in rec if tags[i][k] == "-"),
                        "negAvg": round(sum((revs[i].get("r") or 0) for i in neg) / len(neg), 1) if neg else None}
        rated = [r["r"] for r in revs.values() if r.get("r")]
        rrated = [r["r"] for r in recent if r.get("r")]
        summary = {"analyzed": len(revs), "tagged": len(tags), "avg": round(sum(rated) / len(rated), 2) if rated else None,
                   "recentAvg": round(sum(rrated) / len(rrated), 2) if rrated else None, "recentN": len(recent),
                   "low": round(100 * sum(1 for x in rated if x <= 6) / len(rated), 1) if rated else None,
                   "period": f"{dates[0][:4]}–{last[:4]}" if dates else "", "last": last}
        (hd / "stats.json").write_text(json.dumps({"summary": summary, "cats": stats}, ensure_ascii=False, indent=1), encoding="utf-8")
        notes = _read_notes(hd, revs)
        (hd / "notes.json").write_text(json.dumps({k: v for k, v in notes.items() if v["good"] or v["bad"] or v["quotes"]},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\n{meta['name']} (id {hid}): прочитано {len(revs)}, размечено {len(tags)}, средняя {summary['avg']}, "
            f"за последний год {summary['recentAvg']} ({summary['recentN']} отз.), низких (≤6) {summary['low']} %")
        for k, v in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
            log(f"  {CAT_TITLE[k]:<34} {v['n']:>5} отз. ({v['share']:>4} %)  + {v['pos']:<4} − {v['neg']:<4} ~ {v['mix']:<4}"
                f"  за год: + {v['rpos']} − {v['rneg']}")
    log("\nРАЗМЕТКА ПОЛНАЯ" if ok else "\nЕСТЬ ПРОПУСКИ — доразметь указанные части и запусти tally ещё раз")


# ---------------------------------------------------------------- отправка

def _strs(v, limit=40):
    return [_clean(x) for x in (v or []) if _clean(x)][:limit] if isinstance(v, list) else []


def cmd_put(rid):
    base = WORK / rid
    req = json.loads((base / "request.json").read_text(encoding="utf-8"))
    src = json.loads((base / "report.json").read_text(encoding="utf-8"))
    order = req["hotels"]
    H, problems = [], []
    for hid in order:
        hd = base / str(hid)
        meta = json.loads((hd / "meta.json").read_text(encoding="utf-8"))
        st = json.loads((hd / "stats.json").read_text(encoding="utf-8"))
        notes = json.loads((hd / "notes.json").read_text(encoding="utf-8")) if (hd / "notes.json").exists() else {}
        mine = (src.get("hotels") or {}).get(str(hid)) or {}
        if not mine: problems.append(f"нет текста для отеля {hid}")
        cats = []
        for k in CAT_KEYS:
            num = st["cats"].get(k)
            txt = (mine.get("cats") or {}).get(k) or {}
            if not num and not txt: continue
            if num and not txt and num["n"] >= 3: problems.append(f"{hid}: нет текста по теме {k} ({num['n']} отзывов)")
            fallback = notes.get(k) or {}
            quotes = txt.get("quotes") if isinstance(txt.get("quotes"), list) else (fallback.get("quotes") or [])[:3]
            revq = []
            for q in quotes[:6]:
                if not isinstance(q, dict) or not _clean(q.get("t")): continue
                ref = next((x for x in fallback.get("quotes") or [] if x.get("i") == q.get("i")), {}) if q.get("i") else {}
                revq.append({"t": _clean(q["t"])[:400], "s": q.get("s") if q.get("s") in ("+", "-", "~") else ref.get("s", "~"),
                             "r": q.get("r") or ref.get("r"), "d": q.get("d") or ref.get("d")})
            cats.append({"key": k, "title": CAT_TITLE[k], **(num or {"n": 0, "pos": 0, "neg": 0, "mix": 0, "share": 0}),
                         "good": _strs(txt.get("good")) or _strs(fallback.get("good"), 6),
                         "bad": _strs(txt.get("bad")) or _strs(fallback.get("bad"), 6), "quotes": revq})
        cats.sort(key=lambda c: -c["n"])
        secs = []
        for sct in mine.get("sections") or []:
            if not isinstance(sct, dict) or not _clean(sct.get("title")): continue
            secs.append({"title": _clean(sct["title"]), **({"text": _clean(sct["text"])} if _clean(sct.get("text")) else {}),
                         **({"items": _strs(sct.get("items"), 60)} if sct.get("items") else {})})
        sm = st["summary"]
        H.append({"id": hid, "name": meta["name"], "stars": meta.get("stars"), "score": meta.get("score"), "sub": meta.get("sub") or {},
                  "reviews": meta.get("total"), "analyzed": sm["analyzed"], "avg": sm["avg"], "recentAvg": sm["recentAvg"],
                  "recentN": sm["recentN"], "low": sm["low"], "period": sm["period"], "night": meta.get("night"),
                  "km": meta.get("km"), "anchor": meta.get("anchor"), "zone": meta.get("zone"), "open": meta.get("open"),
                  "renov": meta.get("renov"), "url": meta.get("url"),
                  "verdict": _clean(mine.get("verdict")), "fit": _clean(mine.get("fit")), "unfit": _clean(mine.get("unfit")),
                  "pros": _strs(mine.get("pros")), "cons": _strs(mine.get("cons")), "cats": cats, "sections": secs})

    def cell(fn):
        return [fn(h) for h in H]
    fmt = lambda v: f"{v:,}".replace(",", " ")
    def share(k):
        def f(h):
            c = next((c for c in h["cats"] if c["key"] == k), None)
            return f"{round(100 * c['neg'] / max(1, h['analyzed']), 1)} % жалоб" if c and c["neg"] else "жалоб нет"
        return f
    auto = [
        {"group": "Главное", "label": "Цена за ночь на наши даты", "cells": cell(lambda h: f"{fmt(h['night'])} ₽" if h["night"] else "нет цены")},
        {"group": "Главное", "label": "До «нашего» отеля", "cells": cell(lambda h: "это он" if h["anchor"] else (f"{h['km']} км".replace(".", ",") if h["km"] is not None else "—"))},
        {"group": "Главное", "label": "Оценка trip.com", "cells": cell(lambda h: str(h["score"]).replace(".", ",") if h["score"] else "—")},
        {"group": "Главное", "label": "Средняя оценка за последний год", "cells": cell(lambda h: f"{str(h['recentAvg']).replace('.', ',')} ({h['recentN']} отз.)" if h["recentAvg"] else "—")},
        {"group": "Главное", "label": "Отзывов прочитано", "cells": cell(lambda h: f"{fmt(h['analyzed'])} из {fmt(h['reviews'])}")},
        {"group": "Главное", "label": "Открыт / ремонт", "cells": cell(lambda h: " / ".join(x for x in [h.get("open") or "", h.get("renov") or ""] if x) or "—")},
        {"group": "Жалобы в отзывах", "label": "Шум", "cells": cell(share("noise"))},
        {"group": "Жалобы в отзывах", "label": "Чистота", "cells": cell(share("clean"))},
        {"group": "Жалобы в отзывах", "label": "Насекомые", "cells": cell(share("insects"))},
        {"group": "Жалобы в отзывах", "label": "Запахи, сырость", "cells": cell(share("smell"))},
        {"group": "Жалобы в отзывах", "label": "Персонал", "cells": cell(share("staff"))},
    ]
    rows = []
    for r in src.get("compare") or []:
        if not isinstance(r, dict) or not _clean(r.get("label")): continue
        cells = r.get("cells") or {}
        best = [order.index(int(b)) for b in r.get("best") or [] if str(b).isdigit() and int(b) in order]
        rows.append({"group": _clean(r.get("group")) or "Удобства", "label": _clean(r["label"]),
                     "cells": [_clean(cells.get(str(h))) or "—" for h in order], **({"best": best} if best else {})})
    if not rows: problems.append("нет строк сравнения (compare)")
    if not _strs(src.get("summary")): problems.append("нет общего вывода (summary)")
    if problems:
        log("НЕ ОТПРАВЛЯЮ — исправь report.json:\n  " + "\n  ".join(problems)); sys.exit(2)
    out = {"v": 1, "made": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "stop": req["stop"], "title": req["title"], "dates": f"{req['ci']} — {req['co']}", "by": req.get("name"),
           "summary": _strs(src.get("summary")), "picks": [{"title": _clean(p.get("title")), "text": _clean(p.get("text"))}
                                                         for p in src.get("picks") or [] if isinstance(p, dict) and _clean(p.get("title"))],
           "cols": [{"id": h["id"], "name": h["name"]} for h in H], "compare": auto + rows, "hotels": H}
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    (base / "final.json").write_text(body, encoding="utf-8")
    if len(body.encode()) > 900_000:
        log(f"СЛИШКОМ БОЛЬШОЙ ОТЧЁТ ({len(body.encode()) // 1024} КБ, предел 900) — сократи цитаты и списки"); sys.exit(2)
    fs_patch(rid, {"status": "ready", "r": body, "prog": "", "done": now_ms()})
    log(f"ОТПРАВЛЕНО: {len(body.encode()) // 1024} КБ, отелей {len(H)}")


def main(argv):
    if not argv:
        print(__doc__); return
    cmd, rest = argv[0], argv[1:]
    fns = {"queue": cmd_queue, "wait": cmd_wait, "take": cmd_take, "fetch": cmd_fetch, "tally": cmd_tally,
           "put": cmd_put, "status": cmd_status, "fail": cmd_fail}
    if cmd not in fns:
        print(__doc__); sys.exit(1)
    fns[cmd](*rest)
