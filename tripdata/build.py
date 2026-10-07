"""Сборка данных сайтов: отели по городам, цены по остановкам, оценки, плюсы и минусы, «Коротко об отеле»."""
import json, re, os, math, statistics as st
from .analyze import analyze
from .paths import CONTENT, HOTEL_META, TRIP_DATA, SITE_DATA, read_json, write_json, list_path, load_settings, load_hotels

CENTER={"bkk":(13.7465,100.5348),"cm":(18.78775,98.99335),"cr":(19.9076,99.8309),"pt":(12.9276,100.8771)}
def hav(a,b,c,d):
    R=6371;p=math.pi/180
    return 2*R*math.asin(math.sqrt(math.sin((c-a)*p/2)**2+math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2))
def fnum(s):
    try: return float(str(s).replace(",","."))
    except: return None
ZFIX={"Лай Wua":"Вуа Лай","Nawarat":"Наварат","San Phe Suea":"Сан Пхи Суа","Central Festival Chiang Mai":"Central Festival","Дой Су Тхеп":"Дой Сутхеп"}
def zone_of(city,m,lat,lng):
    zn=(m.get("zone") or "").strip()
    if city=="cm": zn=ZFIX.get(zn,zn)
    if city=="cr":
        dc=hav(lat,lng,19.9076,99.8309); da=hav(lat,lng,19.9523,99.8829)
        zn="Центр" if dc<=1.3 else ("Район аэропорта" if da<=3.5 else ("Берег реки Кок" if lat>=19.915 and dc<=5 else "Окраины"))
    if city=="pt" and lng and lng<100.83: zn="Остров Ко Лан"
    if not zn:
        far=hav(lat,lng,*CENTER[city])>6 if lat else False
        zn="Пригород" if (city=="cm" and far) else "Без района на trip.com"
    if city=="cm" and zn in ("Пригород","Без района на trip.com","Сан Пхи Суа"): pass
    for d in G[city]["districts"]:
        if zn in d["z"]: return d["t"]
    return "Без района на trip.com" if city!="cm" else "Пригороды и без района"
POSTXT={"clean":"Чистоту хвалят","loc":"Расположение и транспорт хвалят","staff":"Персонал хвалят","breakfast":"Завтрак хвалят","pool":"Бассейн хвалят","bed":"Удобные кровати отмечают","value":"Соотношение цены и качества хвалят","quiet":"Тишину отмечают","spacious":"Просторные номера отмечают","view":"Вид из окна хвалят"}
CONTXT={"roach":"Тараканы, клопы","damp":"Сырость, плесень, затхлость","smell":"Неприятный запах","dirty":"Грязь, пыль, пятна","noise":"Шум, слабая звукоизоляция","old":"Устаревшее здание и мебель","ac":"Проблемы с кондиционером","bath":"Сантехника и душ","small":"Тесные номера","smoke":"Табачный дым","rude":"Грубость персонала","wifi":"Плохой Wi-Fi","ants":"Муравьи, комары, мошки"}
WEIGHT={"roach":3,"damp":2,"smell":2,"dirty":1.5,"noise":1,"old":1,"ac":1,"bath":1,"small":0.8,"smoke":1,"rude":1.2,"wifi":0.6,"ants":1.2}
AMR=[("pool",r"бассейн"),("gym",r"Фитнес"),("food",r"Ресторан|Restaurant|Кафе|cafe|Снэк-бар"),("bar",r"\bБар\b|Лобби-бар|\bbar\b"),("laundry",r"Прачечн|Услуги прачечной|Стиральный порошок"),("lift",r"^Лифт$"),("parking",r"парковка|Парковка"),("transfer",r"Трансфер от аэропорта"),("kitchen",r"Общая кухня"),("spa",r"Спа|Массаж|Сауна"),("work",r"Коворкинг|Бизнес-центр")]
DORM=re.compile(r"общ\S* (номер|спальн|комнат)|dorm|койк|кровать в |bunk|капсул|capsule|двухъярусн|смешанн|женск\S* (номер|спальн)|мужск\S* (номер|спальн)",re.I)
SHARED=re.compile(r"общ\S* ванн|shared bath|shared toilet|общ\S* санузел|общ\S* туалет",re.I)
NOWIN=re.compile(r"без окна|windowless|no window",re.I)
def pct(x): return f"{round(x*100):d}%" if x>=0.01 else "<1%"
def plural(n,a,b,c):
    n=abs(n)%100;n1=n%10
    if 10<n<20: return c
    if n1==1: return a
    if 2<=n1<=4: return b
    return c
def room_flags(room,name):
    room=room or ""
    private=bool(re.search(r"частн|private|отдельн",room,re.I))
    return bool(DORM.search(room)) and not private, bool(SHARED.search(room)), bool(NOWIN.search(room))
def build_trip(log=print):
  global G
  G=read_json(CONTENT/"guides.json"); SD=read_json(CONTENT/"stops.json"); STOPS=SD["stops"]
  SAVED={k:set(v) for k,v in read_json(CONTENT/"saved.json",{}).items()}
  META={int(k):v for k,v in read_json(HOTEL_META,{}).items()}
  LIMIT=load_settings()["limits"]
  OUT={"cities":{},"stops":[],"guides":G,"tripwide":SD["tripwide"]}
  for city in ("bkk","cm","cr","pt"):
      stops=[s for s in STOPS if s["city"]==city]
      lists={s["id"]:{h["id"]:h for h in read_json(list_path(s["id"]),[])} for s in stops}
      sv=SAVED.get(city,set())
      # отели из плана и сохранённые, которых нет в свежей выдаче: берём последний известный снимок
      # (цену — только если он снят на те же даты, иначе без цены)
      for s in stops:
          for hid in sv|{s["anchor"],s["proposed"]}:
              if hid not in lists[s["id"]] and hid in META:
                  m=dict(META[hid]); same=m.get("_ci")==s["ci"].replace("-","") and m.get("_co")==s["co"].replace("-","")
                  # снимок с других дат: без цены и без акций, но значки и рейтинги отеля оставляем
                  if not same: m.update(night=None,total=None,tags=[],tc={k:v for k,v in (m.get("tc") or {}).items() if k in ("m","a","n","r")})
                  lists[s["id"]][hid]=m
      meta={}
      for s in stops:
          for hid,h in lists[s["id"]].items(): meta.setdefault(hid,h)
      special={s["anchor"] for s in stops}|{s["proposed"] for s in stops}|sv
      ids=set()
      for s in stops:
          for hid,h in lists[s["id"]].items():
              if h.get("night") and h["night"]<=LIMIT.get(city,5000): ids.add(hid)
      ids|=special
      HOT=load_hotels(city)
      ids=[i for i in ids if i in HOT and i in meta]
      AN={}
      for hid in ids:
          rec=HOT[hid]; d={"cr":rec["cr"],"fac":rec["fac"],"total":rec["total"]}; AN[hid]=(rec["a"],d,meta[hid])
      def rate(a,k,src): return a[src][k]/max(1,(a["n"] if src=="c" else a["npos"]))
      keysC=next(iter(AN.values()))[0]["c"].keys(); keysP=next(iter(AN.values()))[0]["p"].keys()
      big=[a for a,_,_ in AN.values() if a["n"]>=30]
      MED_C={k:st.median([rate(a,k,"c") for a in big]) for k in keysC}
      MED_P={k:st.median([rate(a,k,"p") for a in big]) for k in keysP}
      MEDS=st.median([(d.get("cr") or {}).get("ratingAll") for a,d,_ in AN.values() if a["n"]>=30 and (d.get("cr") or {}).get("ratingAll")])
      H=[]
      for hid,(a,d,m) in AN.items():
          n=a["n"]; c=a["c"]; pp=a["p"]; cr=d.get("cr") or {}
          lat=fnum(m.get("lat")); lng=fnum(m.get("lng"))
          score=cr.get("ratingAll") or fnum(m.get("score"))
          pros=[]
          if n>=5:
              cand=[]
              for k,v in pp.items():
                  r=v/max(1,a["npos"])
                  if v>=2 and r>=0.05: cand.append((r>MED_P[k]*1.1,r,k))
              cand.sort(reverse=True)
              for _,r,k in cand[:3]: pros.append(f"{POSTXT[k]} {pct(r)} довольных гостей")
          cons=[]
          if n>=5:
              red=[];other=[]
              for k,v in c.items():
                  if k=="insect": continue
                  r=v/max(1,n)
                  if k in ("roach","ants","smell","damp"):
                      if v>=2 and (r>MED_C[k]*1.15 or v>=4): red.append((WEIGHT[k]*v,k,v,r))
                  elif v>=2 and r>=0.006 and r>MED_C[k]*1.15: other.append((WEIGHT[k]*(r+0.002)/(MED_C[k]+0.004),k,v,r))
              red.sort(reverse=True); other.sort(reverse=True)
              for _,k,v,r in (red+other)[:4]:
                  extra=f", из них {a['crec'][k]} за 2025–2026" if k in ("roach","ants","smell","damp") and a["crec"].get(k) else ""
                  cons.append(f"{CONTXT[k]} — {pct(r)} отзывов" if k=="noise" else f"{CONTXT[k]} — {v} {plural(v,'отзыв','отзыва','отзывов')}{extra}")
          for key,lab in (("ratingFacility","удобства"),("ratingRoom","чистота"),("ratingLocation","расположение")):
              v=cr.get(key)
              if v and v<8.0 and len(cons)<4: cons.append(f"Низкая оценка за {lab}: {str(v).replace('.',',')}")
          if 0<n<30: cons.append(f"Мало отзывов ({n}) — статистика неточная")
          if n==0: cons.append("Отзывов пока нет")
          if not cons: cons.append("Повторяющихся жалоб нет")
          yes=d["fac"]["yes"]; am=[k for k,rx in AMR if any(re.search(rx,f,re.I) for f in yes)]
          comp=None
          if n>=5 and score:
              per=lambda x:x/(n+50)*100
              pen=min(2.0,0.30*per(c["roach"])+0.10*per(c["ants"])+0.12*per(c["smell"])+0.20*per(c["damp"]))
              negsh=a["neg"]/max(1,n)*100
              negadj=max(-0.5,min(0.15,-(negsh-5)*0.03)) if n>=60 else min(0,-(negsh-5)*0.03)
              trend=max(-0.3,min(0.2,(a["recent"]-score)*0.5)) if n>=120 and a["recent"] else 0
              small=-0.3 if n<15 else 0
              base=round((n*score+25*MEDS)/(n+25),2)
              comp=[base,round(-pen,2),round(negadj,2),round(trend,2),small]
          H.append(dict(id=hid,nm=m["name"],cat=m.get("cat") or "",st=m.get("star") or 0,z=zone_of(city,m,lat or 0,lng or 0),la=lat,ln=lng,
            sc=score,cl=cr.get("ratingRoom"),fa=cr.get("ratingFacility"),lo=cr.get("ratingLocation"),se=cr.get("ratingService"),rv=cr.get("showCommentNum") or d.get("total") or 0,an=n,tot=d.get("total") or n,
            ng=round(a["neg"]/max(1,n)*100,1) if n else None,ns=round(c["noise"]/max(1,n)*100,1) if n else None,
            ro=c["roach"],ror=a["crec"]["roach"],at=c["ants"],sm=c["smell"],smr=a["crec"]["smell"],dm=c["damp"],dmr=a["crec"]["damp"],ins=c["insect"],insr=a["crec"]["insect"],
            am=am,amn=len(yes),yr=d["fac"].get("yr",""),ry=d["fac"].get("rn",""),pr=pros,co=cons[:5],cp=comp,sv=int(hid in sv)))
      OUT["cities"][city]={"hotels":H,"meds":MEDS}
      for s in stops:
          L=lists[s["id"]]; P={}
          sp_stop={s["anchor"],s["proposed"]}|sv   # forced only for THIS period: its own plan hotels (+ saved)
          hidset={h["id"] for h in H}
          for hid in hidset:
              m=L.get(hid)
              if not m: continue
              night=m.get("night"); 
              if not night and hid not in sp_stop: continue
              if night and night>LIMIT.get(city,5000) and hid not in sp_stop: continue
              total=m.get("total") or (night*s["nights"] if night else None)
              dorm,shared,nowin=room_flags(m.get("room"),m.get("name"))
              free=any("Бесплатная отмена" in (t or "") for t in m.get("tags") or [])
              # 8-й элемент — отметки trip.com (см. tripcom.marks), 0 если их нет
              P[hid]=[round(total/ s["nights"]) if total else night,total,int(free),m.get("room") or "",int(dorm),int(shared),int(nowin),m.get("tc") or 0]
          an=meta.get(s["anchor"]) or {}
          so=dict(s); so["limit"]=LIMIT.get(city,5000); so["prices"]=P; so["alat"]=fnum(an.get("lat")); so["alng"]=fnum(an.get("lng"))
          OUT["stops"].append(so)
      log(city,"hotels",len(H),"reviews",sum(h["an"] for h in H),"median",MEDS, {s["id"]:len([1 for p in OUT["stops"] if p["id"]==s["id"] for _ in p["prices"]]) for s in stops})
  OUT["stops"].sort(key=lambda s:s["ci"])
  return OUT


# ---------- «Коротко об отеле»: фишки, тревожные сигналы, расположение
from .notes import FEAT, FLAG
FL={k:l for k,l,_ in FEAT}; FW={k:(l,w) for k,l,w,_ in FLAG}
def hav(a,b,c,d):
    R=6371;p=math.pi/180
    return 2*R*math.asin(math.sqrt(math.sin((c-a)*p/2)**2+math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2))
def plural(n,a,b,c):
    n=abs(n)%100;n1=n%10
    if 10<n<20: return c
    if n1==1: return a
    if 2<=n1<=4: return b
    return c
def dist(km): return f"{int(round(km*1000/10)*10)} м" if km<1 else f"{km:.1f} км".replace(".",",")
def how(km,city,island=False):
    if km<=1.2: return f"≈{max(1,round(km*16))} мин пешком"
    if island: return f"≈{round(km*1.4/20*60+2)} мин на мопеде"
    if city=="bkk": return f"≈{round(km*1.4/14*60+4)} мин на такси"
    if city=="pt": return f"≈{round(km*1.3/20*60+3)} мин на такси или сонгтэо"
    return f"≈{round(km*1.3/22*60+3)} мин на Grab"
# ---- Bangkok rail & piers
raw=read_json(CONTENT/"geo"/"bkk_rail.json")["elements"]
ST=[];seen=set()
for e in raw:
    if "lat" not in e: continue
    t=e.get("tags",{})
    if t.get("railway")=="construction" or "construction" in t or t.get("disused") or t.get("abandoned"): continue
    nm=t.get("name:en") or t.get("name")
    if not nm: continue
    ref=t.get("ref",""); net=(t.get("network","")+" "+t.get("operator","")).strip()
    if t.get("amenity")=="ferry_terminal": sysn="пирс"
    elif re.match(r"^(BL|PP|YL|PK|OR)\d",ref) or "MRT" in net or "มหานคร" in net: sysn="MRT"
    elif re.match(r"^A\d",ref) or "ARL" in net or "Airport Rail" in net: sysn="ARL"
    elif re.match(r"^R[NW]\d",ref) or "สายสีแดง" in net: sysn="Red Line"
    elif re.match(r"^G\d",ref) or "Gold" in net: sysn="Gold Line"
    elif "BTS" in net or "Sukhumvit Line" in net or re.match(r"^(N|E|S|W)\d+$|^CEN$",ref) and t.get("station") in ("subway","light_rail","monorail",None) and t.get("railway")=="station": sysn="BTS"
    else: continue
    if sysn=="BTS" and t.get("railway")!="station": continue
    key=(sysn,nm)
    if key in seen: continue
    seen.add(key); ST.append((sysn,nm,e["lat"],e["lon"]))
RAIL=[s for s in ST if s[0]!="пирс"]; PIER=[s for s in ST if s[0]=="пирс"]
CM=[("ворот Тхапхэ",18.78775,98.99335),("Ночного базара",18.7852,99.0003),("Ват Пхра Сингх",18.7884,98.9819),("Ват Чеди Луанг",18.7870,98.9866),("One Nimman",18.8000,98.9677),("ТЦ Maya",18.8025,98.9670),("рынка Варорот",18.7905,99.0002),("ворот Чанг Пуак",18.7957,98.9864),("аэропорта",18.7668,98.9626),("вокзала",18.7848,99.0172),("Central Festival",18.8075,99.0187)]
CR=[("часовой башни",19.9076,99.8309),("Ночного базара",19.9056,99.8347),("автовокзала",19.9058,99.8336),("аэропорта",19.9523,99.8829),("Синего храма",19.9230,99.8418)]
PTL=[("Terminal 21",12.9500,100.8888),("Central Festival",12.9346,100.8836),("Walking Street",12.9271,100.8735),("пирса Бали Хай",12.9205,100.8678),("Святилища Истины",12.9726,100.8890)]
PTB=[("пляжа Вонг Амат",12.9625,100.8850),("Центрального пляжа",12.9420,100.8835),("Центрального пляжа",12.9330,100.8790),("Центрального пляжа",12.9265,100.8735),("пляжа Джомтьен",12.9000,100.8660),("пляжа Джомтьен",12.8850,100.8690),("пляжа Джомтьен",12.8700,100.8750),("пляжа Пратамнак",12.9135,100.8605)]
KLP=[("пирса Таваэн",12.92749,100.77501),("пирса На Бан",12.9208,100.7890)]
KLB=[("пляжа Таваэн",12.9254,100.77809),("пляжа Самэ",12.91193,100.76959),("пляжа Тиен",12.91905,100.76974),("пляжа Нуал",12.90074,100.77741),("пляжа Та Яй",12.93617,100.78807),("пляжа Тонг Ланг",12.9291,100.78343)]
def nearest(L,la,ln): return min(((hav(la,ln,a,b),n) for n,a,b in L),key=lambda x:x[0])
def loc(city,h):
    la,ln=h.get("la"),h.get("ln")
    if not la: return ""
    if city=="bkk":
        d,(sysn,nm)=min(((hav(la,ln,a,b),(s,n)) for s,n,a,b in RAIL),key=lambda x:x[0])
        out=[f"{sysn} {nm} — {dist(d)}, {how(d,city)}" if d<=1.5 else f"до метро далеко: ближайшая станция {sysn} {nm} в {dist(d)}"]
        if PIER:
            pd,pn=min(((hav(la,ln,a,b),n) for s,n,a,b in PIER),key=lambda x:x[0])
            if pd<=0.8 and (pd<d or d>0.8): out.append(f"пирс {pn} — {dist(pd)}")
        return "; ".join(out)
    if city=="cm":
        dT=hav(la,ln,18.78775,98.99335); parts=[f"до ворот Тхапхэ {dist(dT)}, {how(dT,city)}"]
        d2,n2=nearest([x for x in CM if x[0]!="ворот Тхапхэ"],la,ln)
        if d2<=1.5 and d2<dT: parts.insert(0,f"до {n2} {dist(d2)}")
        return "; ".join(parts)
    if city=="cr":
        dC=hav(la,ln,19.9076,99.8309); parts=[f"до часовой башни {dist(dC)}, {how(dC,city)}"]
        d2,n2=nearest(CR[1:],la,ln)
        if d2<dC and d2<=2: parts.insert(0,f"до {n2} {dist(d2)}")
        return "; ".join(parts)
    if city=="pt":
        if ln<100.83:
            db,nb=nearest(KLB,la,ln); dp,np_=nearest(KLP,la,ln)
            return f"до {nb} {dist(db)}, {how(db,city,True)}; до {np_} {dist(dp)}"
        db,nb=nearest(PTB,la,ln); dl,nl=nearest(PTL,la,ln)
        return f"до {nb} {dist(db)}, {how(db,city)}; до {nl} {dist(dl)}"
    return ""


def merge_notes(D, RAW):
    """Накладывает на отели «Чем известен», «Осторожно», «Где» и счётчик балконов."""
    for city,o in D["cities"].items():
        R=RAW[city]; H=o["hotels"]
        big=[R[str(h["id"])] for h in H if R[str(h["id"])][0]>=30]
        MF={k:st.median([b[1][k]/b[0] for b in big]) for k,_,_ in FEAT}
        MR={k:st.median([b[2][k]/b[0] for b in big]) for k,_,_,_ in FLAG}
        for h in H:
            n,fc,rc=R[str(h["id"])]
            fx=[]
            if n>=5:
                for k,l,_ in FEAT:
                    c=fc[k]; s=c/n
                    if c>=(2 if n<40 else 3) and s>=max(0.02,2*MF[k]): fx.append(((s+0.005)/(MF[k]+0.01),l,c,s))
                fx.sort(reverse=True)
            h["fx"]=[(f"{l} — пишут в {round(s*100)}% отзывов" if s>=0.05 else f"{l} ({c} {plural(c,'отзыв','отзыва','отзывов')})") for _,l,c,s in fx[:4]]
            rf=[]
            if n>=5:
                for k,l,w,_ in FLAG:
                    c=rc[k]; s=c/n
                    if k=="notrec":
                        if c>=3 and s>=0.02: rf.append((w*s*50,f"{l} — {c} {plural(c,'отзыв','отзыва','отзывов')}"))
                        continue
                    if c>=2 and (s>=max(0.008,2*MR[k]) or (w>=2.5 and c>=2)): rf.append((w*s*50+w,f"{l} — {c} {plural(c,'отзыв','отзыва','отзывов')}"))
                rf.sort(reverse=True); rf=[x[1] for x in rf[:4]]
                sc=h.get("sc")
                rec=None
            else: rf=[]
            if h.get("ng") is not None and h["ng"]>=15: rf.append(f"негативный каждый {max(2,round(100/h['ng']))}-й отзыв")
            h["rf"]=rf
            h["lc"]=loc(city,h)
            h["bal"]=fc["balcony"]
        print(city,"fx>0:",sum(1 for h in H if h["fx"]),"rf>0:",sum(1 for h in H if h["rf"]),"lc:",sum(1 for h in H if h["lc"]))

    return D


def notes_raw(D):
    """Счётчики фишек и сигналов из кеша отелей (посчитаны при скачивании отзывов)."""
    RAW = {}
    for c in D["cities"]:
        HOT = load_hotels(c)
        RAW[c] = {str(h["id"]): HOT[h["id"]]["nt"] for h in D["cities"][c]["hotels"]}
    return RAW


def export_site(D, out=SITE_DATA, built_at=""):
    """Раскладывает данные для сайтов: index.json, cities/<город>.json, prices/<остановка>.json."""
    stops = []
    for s in D["stops"]:
        s = dict(s); prices = s.pop("prices")
        write_json(out / "prices" / f"{s['id']}.json", prices)
        s["priceCount"] = len(prices); stops.append(s)
    cities = {}
    for c, o in D["cities"].items():
        write_json(out / "cities" / f"{c}.json", o["hotels"])
        cities[c] = {"meds": o["meds"], "count": len(o["hotels"])}
    idx = {"stops": stops, "guides": D["guides"], "tripwide": D["tripwide"], "cities": cities,
           "saved": read_json(CONTENT / "saved.json", {}), "builtAt": built_at,
           "reviews": sum(sum(h["an"] for h in o["hotels"]) for o in D["cities"].values())}
    write_json(out / "index.json", idx)
    return idx
