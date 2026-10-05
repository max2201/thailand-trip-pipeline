"""Запросы к trip.com: выдача отелей с ценами, все отзывы, удобства."""
import copy, json, math, re, time
import requests
from .paths import CONTENT, read_json

H = {"content-type": "application/json", "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
     "origin": "https://ru.trip.com", "referer": "https://ru.trip.com/", "accept-language": "ru-RU"}
LIST_URL = "https://ru.trip.com/restapi/soa2/34951/fetchHotelList"
URL = "https://ru.trip.com/restapi/soa2/34308/getHotelCommentInfo"
HEAD = {"platform": "PC", "cver": "0", "bu": "IBU", "group": "trip", "locale": "ru-RU", "timezone": "7", "currency": "RUB", "pageId": "10320668147", "isSSR": False}


def parse(it):
    hi=it["hotelInfo"]; pos=hi.get("positionInfo",{}); ci=hi.get("commentInfo",{}) or {}
    mc=(pos.get("mapCoordinate") or [{}])[0]
    rooms=it.get("roomInfo") or [{}]; r=rooms[0]; pi=r.get("priceInfo",{}) or {}
    tags=[t.get("tagTitle") for t in (r.get("roomTags",{}) or {}).get("advantageTags",[]) or []]
    tot=None
    import re
    m=re.search(r"Итого:\s*([\d\s ]+)₽",pi.get("priceExplanation","") or "")
    if m: tot=int(re.sub(r"\D","",m.group(1)))
    return {"id":int(hi["summary"]["hotelId"]),"name":hi["nameInfo"]["name"],"cat":(hi.get("hotelCategory") or {}).get("categoryName"),
      "star":(hi.get("hotelStar") or {}).get("star"),"lat":mc.get("latitude"),"lng":mc.get("longitude"),"zone":pos.get("address"),"zones":pos.get("zoneNames"),
      "score":ci.get("commentScore"),"cnt":ci.get("commenterNumber"),"night":pi.get("price"),"total":tot,"room":(r.get("summary") or {}).get("physicsName"),"tags":tags,"status":(hi.get("statusInfo") or {}).get("status")}


def crawl_list(city_id, ci, co, log=print, max_pages=400):
    """Вся выдача города на даты ci–co (YYYYMMDD): отели, цены, координаты."""
    base = read_json(CONTENT / "list_request.json")
    base["destination"]["geo"]["cityId"] = city_id
    base["date"]["dateInfo"]["checkInDate"] = ci
    base["date"]["dateInfo"]["checkOutDate"] = co
    for e in base["head"].get("extension", []):
        if e["name"] == "cityId": e["value"] = str(city_id)
        if e["name"] == "checkIn": e["value"] = f"{ci[:4]}-{ci[4:6]}-{ci[6:]}"
        if e["name"] == "checkOut": e["value"] = f"{co[:4]}-{co[4:6]}-{co[6:]}"
    h2 = dict(H); h2["referer"] = f"https://ru.trip.com/hotels/list?city={city_id}"
    out, page, fails = {}, 1, 0
    while page <= max_pages and fails <= 8:
        req = copy.deepcopy(base); req["paging"]["pageSize"] = 20; req["paging"]["pageIndex"] = page
        d = None
        for _ in range(4):
            try:
                d = requests.post(LIST_URL, json=req, headers=h2, timeout=60).json().get("data")
                if d is not None: break
            except Exception:
                pass
            time.sleep(2)
        if not d:
            fails += 1; page += 1; continue
        info = d.get("hotelListAddtionInfo", {}); hl = d.get("hotelList") or []
        for it in hl:
            try:
                p = parse(it); out[p["id"]] = p
            except Exception:
                pass
        if page % 20 == 0: log(f"  страница {page}, отелей {len(out)}")
        if info.get("isLastPage") or not hl: break
        page += 1; time.sleep(0.3)
    return list(out.values())


def api(hid,page):
    for _ in range(4):
        try:
            r=requests.post(URL,json={"hotelId":hid,"commentFilterOptions":{"pageIndex":page,"pageSize":50,"repeatComment":1},"sceneTypes":["CommentList"],"head":HEAD},headers=H,timeout=60)
            d=r.json().get("data")
            if d is not None: return d
        except Exception: pass
        time.sleep(2)
    return None
def fac(hid):
    for t in range(3):
        try:
            h=requests.get(f"https://ru.trip.com/hotels/detail/?hotelId={hid}&locale=ru-RU&curr=RUB",headers=H,timeout=60).text
            names=list(dict.fromkeys(re.findall(r'facilityDesc\\+":\\+"([^\\"]+)',h)))
            yes,no=[],[]
            for n in names:
                i=h.find('facilityDesc\\":\\"'+n); (no if 'defect' in h[i:i+220] else yes).append(n)
            a=re.findall(r'address\\?"\s*:\s*\\?"([^"\\]{5,160})',h)
            yr=re.findall(r'(?:Год открытия|Открыт)[^0-9]{0,30}(\d{4})',h)
            return {"yes":yes,"no":no,"addr":a[0] if a else "","yr":yr[0] if yr else ""}
        except Exception: pass
        time.sleep(2)
    return {"yes":[],"no":[],"addr":"","yr":""}


def fetch_reviews(hid, max_pages=20):
    """Все отзывы отеля (для огромных — max_pages*50 самых свежих) + удобства."""
    d1 = api(hid, 1)
    if not d1:
        return None
    total = d1.get("totalCountForPage") or 0
    pages = min(math.ceil(total / 50), max_pages)
    cl = list((d1.get("groupList") or [{}])[0].get("commentList", []))
    for p in range(2, pages + 1):
        d = api(hid, p)
        if d: cl += (d.get("groupList") or [{}])[0].get("commentList", [])
        time.sleep(0.15)
    rev = [{"t": (c.get("translatedContent") or c.get("content") or ""), "r": c.get("rating"), "d": (c.get("createDate") or "")[:10], "lang": c.get("language")} for c in cl]
    return {"total": total, "tags": [[t.get("name"), t.get("commentCount"), t.get("type")] for t in d1.get("commentTagList") or []],
            "cr": d1.get("commentRating"), "rev": rev, "fac": fac(hid)}
