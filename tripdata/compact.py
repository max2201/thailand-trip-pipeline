"""Из сырых отзывов отеля — компактная запись для кеша. Тексты отзывов не сохраняются."""
from .analyze import analyze
from .notes import count_notes


def compact(hid, d):
    n, fc, rc = count_notes(d)
    fac = d.get("fac") or {}
    return {"id": hid, "a": analyze(d), "cr": d.get("cr"), "total": d.get("total") or 0,
            "fac": {"yes": fac.get("yes", []), "yr": fac.get("yr", "")}, "nt": [n, fc, rc]}
