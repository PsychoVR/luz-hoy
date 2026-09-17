#!/usr/bin/env python3
"""
fetch_pvpc.py — Descarga el PVPC (ESIOS 1001, Península) de hoy y de mañana,
calcula las mejores franjas y escribe docs/data.json para la web.
Se ejecuta desde GitHub Actions; también vale en local: ESIOS_TOKEN=... python3 fetch_pvpc.py
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

# Ajusta a tu rutina. Orden = prioridad de asignación.
APPLIANCES = [
    {"key": "horno",        "nombre": "Horno",           "dur": 1.0, "ventanas": [(11, 15), (18, 22)], "kw": 2.5},
    {"key": "lavadora",     "nombre": "Lavadora",        "dur": 2.0, "ventanas": [(0, 24)],            "kw": 2.0},
    {"key": "secador",      "nombre": "Secador",         "dur": 1.5, "ventanas": [(7, 23)],            "kw": 2.5, "despues_de": "lavadora"},
    {"key": "lavavajillas", "nombre": "Lavavajillas",    "dur": 2.0, "ventanas": [(0, 24)],            "kw": 2.0},
    {"key": "carga",        "nombre": "Cargar móviles y portátiles", "dur": 3.0, "ventanas": [(0, 24)], "kw": 0.3},
]
POTENCIA_CONTRATADA_KW = 4.6  # tu potencia contratada; no se programan aparatos que la superen a la vez


def fetch_pvpc(target: date):
    start = datetime.combine(target, datetime.min.time(), TZ)
    end = start + timedelta(days=1) - timedelta(minutes=1)
    r = requests.get(ESIOS_URL, timeout=30,
        headers={"Accept": "application/json; application/vnd.esios-api-v1+json",
                 "Content-Type": "application/json", "x-api-key": TOKEN},
        params={"start_date": start.isoformat(), "end_date": end.isoformat(),
                "time_trunc": "hour", "time_agg": "average", "geo_ids[]": GEO_ID})
    if r.status_code in (401, 403):
        sys.exit(f"Token ESIOS rechazado (HTTP {r.status_code}). Revisa el secreto ESIOS_TOKEN.")
    r.raise_for_status()
    by_hour = {}
    for v in r.json()["indicator"]["values"]:
        if v.get("geo_id") not in (None, GEO_ID):
            continue
        dt = datetime.fromisoformat(v["datetime"]).astimezone(TZ)
        if dt.date() == target:
            by_hour[dt.hour] = round(v["value"] / 1000.0, 5)  # €/kWh
    if len(by_hour) < 23:
        raise RuntimeError(f"{target}: solo {len(by_hour)} horas publicadas")
    return [by_hour.get(h, by_hour.get(h - 1, by_hour.get(h + 1))) for h in range(24)]


def fits(load, kw, start, end):
    return all(load.get(h, 0) + kw <= POTENCIA_CONTRATADA_KW for h in range(start, end))


def windows(prices, a, load, not_before=0):
    n = math.ceil(a["dur"])
    for s in range(not_before, 24 - n + 1):
        if any(lo <= s < hi for lo, hi in a["ventanas"]) and fits(load, a["kw"], s, s + n):
            yield {"start": s, "end": s + n, "cost": sum(prices[h] for h in range(s, s + n))}


def best_window(prices, a, load, not_before=0):
    return min(windows(prices, a, load, not_before), key=lambda w: w["cost"], default=None)


def occupy(load, a, w):
    for h in range(w["start"], w["end"]):
        load[h] = load.get(h, 0) + a["kw"]


def describe(prices, a, w):
    n = w["end"] - w["start"]
    worst = max(sum(prices[h:h + n]) for h in range(0, 24 - n + 1))
    return {"key": a["key"], "nombre": a["nombre"], "start": w["start"], "end": w["end"],
            "precio_medio": round(w["cost"] / n, 5), "ahorro_pct": round((1 - w["cost"] / worst) * 100)}


def plan(prices):
    """Asigna franjas en orden de prioridad respetando la potencia contratada.
    Un aparato con dependiente (lavadora -> secador) se optimiza junto con él."""
    load, out, done = {}, [], set()
    dependents = {a["despues_de"]: a for a in APPLIANCES if a.get("despues_de")}
    for a in APPLIANCES:
        if a["key"] in done:
            continue
        dep = dependents.get(a["key"])
        if dep:
            best = None
            for w in windows(prices, a, load):
                l2 = dict(load); occupy(l2, a, w)
                w2 = best_window(prices, dep, l2, not_before=w["end"])
                if w2 and (best is None or w["cost"] + w2["cost"] < best[0]):
                    best = (w["cost"] + w2["cost"], w, w2)
            if best:
                _, w, w2 = best
                out.append(describe(prices, a, w)); occupy(load, a, w)
                out.append(describe(prices, dep, w2)); occupy(load, dep, w2)
                done.update([a["key"], dep["key"]])
                continue
        w = best_window(prices, a, load) or best_window(prices, {**a, "ventanas": [(0, 24)]}, {})
        out.append(describe(prices, a, w)); occupy(load, a, w); done.add(a["key"])
    return out


def main():
    data = {"updated": None, "days": {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    today = datetime.now(TZ).date()
    got = 0
    for target in (today, today + timedelta(days=1)):
        key = target.isoformat()
        if key in data["days"]:
            got += 1; continue
        for attempt in range(3):
            try:
                prices = fetch_pvpc(target)
                data["days"][key] = {"prices": prices, "plan": plan(prices)}
                got += 1; print(f"OK {key}"); break
            except Exception as e:
                print(f"{key}: {e}", file=sys.stderr)
                if attempt < 2 and target != today:
                    time.sleep(300)  # 5 min; el de mañana puede tardar en publicarse
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
