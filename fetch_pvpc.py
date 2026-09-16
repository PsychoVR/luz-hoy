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
EVITAR_SOLAPE_KW = 1.5


def fetch_pvpc(target: date):
    start = datetime.combine(target, datetime.min.time(), TZ)
    end = start + timedelta(days=1) - timedelta(minutes=1)
    r = requests.get(ESIOS_URL, timeout=30,
        headers={"Accept": "application/json; application/vnd.esios-api-v1+json",
                 "Content-Type": "application/json", "x-api-key": TOKEN},
        params={"start_date": start.isoformat(), "end_date": end.isoformat(),
                "time_trunc": "hour", "time_agg": "average", "geo_ids[]": GEO_ID})
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


def best_window(prices, dur_h, ventanas, busy, not_before=0):
    n = math.ceil(dur_h); best = None
    for s in range(not_before, 24 - n + 1):
        hrs = range(s, s + n)
        if not any(a <= s < b for a, b in ventanas) or any(h in busy for h in hrs):
            continue
        cost = sum(prices[h] for h in hrs)
        if best is None or cost < best["cost"]:
            best = {"start": s, "end": s + n, "cost": cost}
    return best


def plan(prices):
    busy, out = set(), []
    done = {}
    for a in APPLIANCES:
        nb = done[a["despues_de"]]["end"] if a.get("despues_de") in done else 0
        w = best_window(prices, a["dur"], a["ventanas"], busy, nb) or best_window(prices, a["dur"], [(0, 24)], set())
        n = w["end"] - w["start"]
        worst = max(sum(prices[h:h + n]) for h in range(0, 24 - n + 1))
        item = {"key": a["key"], "nombre": a["nombre"], "start": w["start"], "end": w["end"],
                "precio_medio": round(w["cost"] / n, 5), "ahorro_pct": round((1 - w["cost"] / worst) * 100)}
        done[a["key"]] = item; out.append(item)
        if a["kw"] >= EVITAR_SOLAPE_KW:
            busy.update(range(w["start"], w["end"]))
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
