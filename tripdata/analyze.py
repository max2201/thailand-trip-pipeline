import json, re, os, math, statistics as st
NEG={
"roach":r"таракан|\bклоп|cockroach|\broach|bed ?bug",
"ants":r"муравь|комар|мошк|насеком|\bants?\b|mosquito|insect",
"smell":r"запах|пахн|воня|вонь|канализ|\bsmell|odou?r|stink",
"damp":r"сырост|сыро[\s,.!]|сыроват|отсырел|плесен|грибок|затхл|влажн\S* (полотенц|бель|постел|стен|простын|кроват|номер)|\bmou?ld|\bdamp|musty",
"noise":r"шум|звукоизол|громк|слышн|noisy|noise|thin walls",
"dirty":r"грязн|пыл[ьи]|пятн|волос[ыа]? (в|на)|dirty|stain|dusty",
"old":r"устарел|изношен|облезл|ржав|обшарпан|стар\S* мебел|стар\S* (отель|здани|номер|ремонт)|отель стар|rusty|outdated|run-?down",
"ac":r"кондиционер|air ?con",
"small":r"маленьк\S* номер|номер\S* (был |очень |довольно |слишком |немного )?маленьк|тесн|cramped|tiny room|small room",
"bath":r"душ|слив|сантехник|унитаз|горяч\S* вод|протек|напор",
"smoke":r"сигарет|курени|курят|табач|smok",
"rude":r"груб|хамск|невежлив|\brude",
"wifi":r"wi-?fi|вай-?фай|интернет",
}
PRE=re.compile(r"(?:^|\s)(без|нет|никаких|никакого|ни малейш\S*|ни одного|ни|не было|не заметил\S*|не почувствовал\S*|не встреча\S*|не видел\S*|не увидел\S*|приятн\S*|свеж\S*|вкусн\S*|отсутстви\S*|no|not any|fresh|without)\s+(\S+\s+){0,2}$",re.I)
POST=re.compile(r"^[^\s,.;!?]*\s+([^\s,.;!?]+\s+)?(нет\b|не было|не видел|не встреча|исчез|не замеч)",re.I)
POSWORD=re.compile(r"вкусно пахн|приятн\S* запах|smell\S* (delicious|good|great|nice)|fresh smell|хлор|chlorine|аромат",re.I)
NEGATED_CATS=("roach","ants","smell","damp","noise","dirty","smoke")
CR={k:re.compile(v,re.I) for k,v in NEG.items()}
def hit(k,t):
    for m in CR[k].finditer(t):
        if k in NEGATED_CATS:
            if PRE.search(t[max(0,m.start()-40):m.start()]) or POST.search(t[m.end():m.end()+30]): continue
        if k=="smell" and POSWORD.search(t[max(0,m.start()-30):m.end()+30]): continue
        return True
    return False
POS={"clean":r"(?<!не)чист|clean","loc":r"располож|локаци|метро|bts|mrt|станци|location","staff":r"персонал|сотрудник|ресепшн|обслуживан|хозя|staff","breakfast":r"завтрак|breakfast","pool":r"бассейн|pool","bed":r"удобн\S* (кроват|матрас|подушк)|кроват\S* (удобн|мягк|больш)|comfortable bed","value":r"соотношен|недорог|цена.{0,25}(хорош|отличн|низк|доступн|приемлем)|дешев|value for money","quiet":r"тих[оиа]|спокойн|quiet","spacious":r"просторн|больш\S* номер|номер\S* больш|spacious","view":r"вид из окна|вид на|видом|great view"}
CP={k:re.compile(v,re.I) for k,v in POS.items()}
LOWONLY=("ac","small","bath","wifi","rude")  # считаем только в отзывах с оценкой <=7
def analyze(d):
    rv=[x for x in d["rev"] if x["t"] and len(x["t"].strip())>3]
    n=len(rv); pos=[x for x in rv if (x["r"] or 0)>=8]; low=[x for x in rv if (x["r"] or 10)<=7]; neg=[x for x in rv if (x["r"] or 10)<=6]
    rec=[x for x in rv if x["d"]>="2025-01-01"]
    c={}; crec={}
    for k in NEG:
        src=low if k in LOWONLY else rv
        c[k]=sum(1 for x in src if hit(k,x["t"]))
    for k in ("roach","ants","smell","damp"): crec[k]=sum(1 for x in rec if hit(k,x["t"]))
    c["insect"]=sum(1 for x in rv if hit("roach",x["t"]) or hit("ants",x["t"]))
    crec["insect"]=sum(1 for x in rec if hit("roach",x["t"]) or hit("ants",x["t"]))
    p={k:sum(1 for x in pos if CP[k].search(x["t"])) for k in POS}
    rec60=sorted(d["rev"],key=lambda x:x["d"],reverse=True)[:60]
    recent=round(sum((x["r"] or 0) for x in rec60)/max(1,len(rec60)),1) if rec60 else None
    dates=[x["d"] for x in rv if x["d"]]
    return dict(n=n,npos=len(pos),nlow=len(low),neg=len(neg),c=c,crec=crec,p=p,recent=recent,dmax=max(dates) if dates else "")
