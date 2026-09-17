#!/usr/bin/env python3
"""
fetch_pvpc.py — Descarga el PVPC cuarto-horario (ESIOS 1001, Península) de hoy y de
mañana, optimiza las franjas de cada electrodoméstico en tramos de 15 min y escribe
docs/data.json para la web. Se ejecuta desde GitHub Actions; en local:
    ESIOS_TOKEN=... python3 fetch_pvpc.py
"""
import json, math, os, sys, time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import requests

TZ = ZoneInfo("Europe/Madrid")
GEO_ID = 8741  # Península
ESIOS_URL = "https://api.esios.ree.es/indicators/1001"
TOKEN = os.environ["ESIOS_TOKEN"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")
KEEP_DAYS = 3
SLOTS = 96                    # tramos de 15 min
POTENCIA_CONTRATADA_KW = 4.6  # tu potencia contratada (está en la factura)

# ---------------------------------------------------------------------------
# Electrodomésticos. dur en horas (múltiplos de 0.25), ventanas = horas en las
# que puede EMPEZAR [ini, fin), kw = potencia aprox., despues_de = dependencia.
# ---------------------------------------------------------------------------
APPLIANCES = [
    {"key": "horno",        "nombre": "Horno",                  "dur": 1.0,  "kw": 2.5, "ventanas": [(11, 15), (18, 22)]},
    {"key": "lavadora",     "nombre": "Lavadora",               "dur": 2.0,  "kw": 2.0, "ventanas": [(0, 24)]},
    {"key": "secadora",     "nombre": "Secadora",               "dur": 2.5,  "kw": 2.5, "ventanas": [(7, 23)], "despues_de": "lavadora"},
    {"key": "secador",      "nombre": "Secador de pelo",        "dur": 0.25, "kw": 2.0, "ventanas": [(7, 10), (19, 23)]},
    {"key": "lavavajillas", "nombre": "Lavavajillas",           "dur": 2.0,  "kw": 2.0, "ventanas": [(0, 24)]},
    {"key": "carga",        "nombre": "Cargar móviles y portátiles", "dur": 3.0, "kw": 0.3, "ventanas": [(0, 24)]},
]


# ---------------------------------------------------------------------------
# 1. Precios
# ---------------------------------------------------------------------------
def fetch_pvpc(target: date):
    """Devuelve 96 precios €/kWh (uno por cuarto de hora) del día target."""
    start = datetime.combine(target, datetime.min.time(), TZ)
    end = start + timedelta(days=1) - timedelta(minutes=1)
    r = requests.get(ESIOS_URL, timeout=30,
        headers={"Accept": "application/json; application/vnd.esios-api-v1+json",
                 "Content-Type": "application/json", "x-api-key": TOKEN},
        params={"start_date": start.isoformat(), "end_date": end.isoformat(), "geo_ids[]": GEO_ID})
    if r.status_code in (401, 403):
        sys.exit(f"Token ESIOS rechazado (HTTP {r.status_code}). Revisa el secreto ESIOS_TOKEN.")
    r.raise_for_status()
    by_slot = {}
    for v in r.json()["indicator"]["values"]:
        if v.get("geo_id") not in (None, GEO_ID):
            continue
        dt = datetime.fromisoformat(v["datetime"]).astimezone(TZ)
        if dt.date() == target:
            by_slot[dt.hour * 4 + dt.minute // 15] = v["value"] / 1000.0
    if len(by_slot) < 23:
        raise RuntimeError(f"{target}: solo {len(by_slot)} valores publicados")
    if len(by_slot) <= 25:  # vino horario: expandir a cuartos
        by_slot = {h * 4 + q: p for h, p in by_slot.items() for q in range(4)}
    prices, last = [], None
    for s in range(SLOTS):  # rellena huecos (cambio de hora) con el vecino
        last = by_slot.get(s, last if last is not None else next(iter(by_slot.values())))
        prices.append(round(last, 5))
    return prices


# ---------------------------------------------------------------------------
# 2. Optimizador en tramos de 15 min
# ---------------------------------------------------------------------------
def slots_of(a):
    return int(round(a["dur"] * 4))


def cost_of(prices, a, s):
    """Coste en € de encender a en el tramo s (kW × h × €/kWh)."""
    return a["kw"] * 0.25 * sum(prices[s:s + slots_of(a)])


def starts(a, load, not_before=0):
    n = slots_of(a)
    for s in range(not_before, SLOTS - n + 1):
        hour = s / 4
        if not any(lo <= hour < hi for lo, hi in a["ventanas"]):
            continue
        if all(load[i] + a["kw"] <= POTENCIA_CONTRATADA_KW for i in range(s, s + n)):
            yield s


def best_start(prices, a, load, not_before=0):
    return min(starts(a, load, not_before), key=lambda s: cost_of(prices, a, s), default=None)


def apply(load, a, s, sign=1):
    for i in range(s, s + slots_of(a)):
        load[i] += sign * a["kw"]


def plan(prices):
    by_key = {a["key"]: a for a in APPLIANCES}
    dependents = {a["despues_de"]: a for a in APPLIANCES if a.get("despues_de")}
    parents = {a["key"]: by_key[a["despues_de"]] for a in APPLIANCES if a.get("despues_de")}
    load = [0.0] * SLOTS
    assign = {}  # key -> tramo de inicio

    # Pasada inicial: por orden de energía (kW·h) descendente, parejas en conjunto
    for a in sorted(APPLIANCES, key=lambda x: -x["kw"] * x["dur"]):
        if a["key"] in assign or a["key"] in parents:
            continue
        dep = dependents.get(a["key"])
        if dep:
            best = None
            for s in starts(a, load):
                apply(load, a, s)
                s2 = best_start(prices, dep, load, not_before=s + slots_of(a))
                if s2 is not None:
                    c = cost_of(prices, a, s) + cost_of(prices, dep, s2)
                    if best is None or c < best[0]:
                        best = (c, s, s2)
                apply(load, a, s, -1)
            if best:
                assign[a["key"]] = best[1]; apply(load, a, best[1])
                assign[dep["key"]] = best[2]; apply(load, dep, best[2])
                continue
        s = best_start(prices, a, load)
        if s is None:  # último recurso: ignorar la potencia
            s = min(range(SLOTS - slots_of(a) + 1), key=lambda x: cost_of(prices, a, x))
        assign[a["key"]] = s; apply(load, a, s)

    # Mejora iterativa: recolocar cada aparato dados los demás hasta converger
    for _ in range(10):
        improved = False
        for a in APPLIANCES:
            k = a["key"]; s_old = assign[k]
            apply(load, a, s_old, -1)
            nb = 0
            if k in parents:
                p = parents[k]; nb = assign[p["key"]] + slots_of(p)
            cands = list(starts(a, load, nb))
            if k in dependents:  # no dejar al dependiente sin sitio
                cands = [s for s in cands if s + slots_of(a) <= assign[dependents[k]["key"]]]
            s_new = min(cands, key=lambda s: cost_of(prices, a, s), default=s_old)
            if cost_of(prices, a, s_new) < cost_of(prices, a, s_old) - 1e-9:
                assign[k] = s_new; improved = True
            apply(load, a, assign[k])
        if not improved:
            break

    out = []
    for a in APPLIANCES:
        s = assign[a["key"]]; n = slots_of(a)
        worst_s = max(range(SLOTS - n + 1), key=lambda x: sum(prices[x:x + n]))
        worst = sum(prices[worst_s:worst_s + n])
        mean = sum(prices[s:s + n]) / n
        out.append({"key": a["key"], "nombre": a["nombre"], "kw": a["kw"], "dur": a["dur"],
                    "start": s / 4, "end": (s + n) / 4,
                    "precio_medio": round(mean, 5),
                    "coste_eur": round(cost_of(prices, a, s), 3),
                    "coste_peor_eur": round(cost_of(prices, a, worst_s), 3),
                    "peor_start": worst_s / 4,
                    "ahorro_pct": round((1 - mean * n / worst) * 100)})
    return out


# ---------------------------------------------------------------------------
def main():
    data = {"updated": None, "days": {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    today = datetime.now(TZ).date()
    got = 0
    for target in (today, today + timedelta(days=1)):
        key = target.isoformat()
        existing = data["days"].get(key)
        if existing and "prices15" in existing and existing.get("v") == 2:
            got += 1; continue
        for attempt in range(3):
            try:
                p15 = fetch_pvpc(target)
                hourly = [round(sum(p15[h * 4:h * 4 + 4]) / 4, 5) for h in range(24)]
                data["days"][key] = {"v": 2, "prices": hourly, "prices15": p15, "plan": plan(p15)}
                got += 1; print(f"OK {key}"); break
            except Exception as e:
                print(f"{key}: {e}", file=sys.stderr)
                if attempt < 2 and target != today:
                    time.sleep(300)
                else:
                    break
    if not got:
        sys.exit("Sin datos")
    keep = sorted(data["days"])[-KEEP_DAYS:]
    data["days"] = {k: data["days"][k] for k in keep}
    data["updated"] = datetime.now(TZ).isoformat(timespec="minutes")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
