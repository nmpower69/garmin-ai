#!/usr/bin/env python3
"""
Tomorrow's ride briefing — rules-based, data-driven, no API key needed.

Reads garmin/data.json (+ power_curves.json for NP) and writes:
  garmin/tomorrow_briefing.json — structured plan for the dashboard
  garmin/tomorrow_briefing.md   — human-readable plan (committed)

Inputs considered:
  - Past 10 road_biking rides: distance, duration, avg HR, NP, gaps between rides
  - Today's ride (if any): distance + intensity feeds directly into the verdict
  - Last 7d wellness: training readiness, HRV, RHR, sleep, stress, body battery
  - Usual bedtime derived from recent sleep-start timestamps (no assumed wake time)

Verdicts: ride (endurance or intervals) / easy (recovery spin) / rest.
Fueling uses the rider's actual kit: bananas, Fast&Up energy gels, Fast&Up Reload.
Always exits 0 — on any error it writes a safe fallback briefing instead.
"""

import json
import datetime
from pathlib import Path

DATA = Path("garmin/data.json")
CURVES = Path("garmin/power_curves.json")
OUT_JSON = Path("garmin/tomorrow_briefing.json")
OUT_MD = Path("garmin/tomorrow_briefing.md")

Z1_TOP, Z2_TOP, Z3_TOP, Z4_TOP = 139, 159, 167, 172


def load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def today_ist():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return datetime.date.today()


def parse_sleep_hours(s):
    # "6h 38m" -> 6.63 ; "N/A"/None -> None
    if not isinstance(s, str):
        return None
    try:
        h, m = 0.0, 0.0
        for part in s.replace("  ", " ").split():
            if part.endswith("h"):
                h = float(part[:-1])
            elif part.endswith("m"):
                m = float(part[:-1])
        tot = h + m / 60.0
        return tot if tot > 0 else None
    except Exception:
        return None


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def usual_bedtime(daily, dates):
    """Median lights-out HH:MM from recent sleep-start timestamps (local wall time)."""
    mins = []
    for d in dates[-14:]:
        try:
            ts = daily[d]["_raw"]["sleep"]["dailySleepDTO"]["sleepStartTimestampLocal"]
            if not isinstance(ts, (int, float)):
                continue
            dt = datetime.datetime.utcfromtimestamp(ts / 1000.0)
            m = dt.hour * 60 + dt.minute
            if dt.hour < 12:  # after midnight -> same night
                m += 1440
            mins.append(m)
        except Exception:
            continue
    if not mins:
        return None
    mins.sort()
    med = mins[len(mins) // 2] % 1440
    h, m = divmod(int(med), 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def build():
    data = load_json(DATA)
    if not data or not data.get("daily"):
        raise ValueError("garmin/data.json missing or empty — run sync first")
    curves = load_json(CURVES) or {}
    daily = data["daily"]
    dates = sorted(daily.keys())
    acts = sorted(
        [a for a in data.get("activities", []) if a.get("sport") == "road_biking"],
        key=lambda x: (x.get("date", ""), str(x.get("id", ""))),
    )
    last10 = acts[-10:]
    today = today_ist()
    tomorrow = today + datetime.timedelta(days=1)

    # ---- ride history facts ----
    def d_dist(a):
        return num(a.get("distance_km"))

    np_by_id = {}
    try:
        for _cid, _c in (curves.get("curves") or {}).items():
            if isinstance(_c, dict):
                _n = num(_c.get("normalizedPower"))
                if _n is not None:
                    np_by_id[str(_cid)] = _n
    except Exception:
        pass

    def is_hard(a):
        # HR-based OR power-based: long intervals can average <167 bpm
        # while still being neuromuscularly hard (high NP).
        if (num(a.get("avg_hr")) or 0) >= 167:
            return True
        _n = np_by_id.get(str(a.get("id") or ""))
        return _n is not None and _n >= 170

    dists = [d_dist(a) for a in last10 if d_dist(a) is not None]
    avg_dist = sum(dists) / len(dists) if dists else 40.0
    d_dates = [datetime.date.fromisoformat(a["date"]) for a in last10 if a.get("date")]
    gaps = [(d_dates[i] - d_dates[i - 1]).days for i in range(1, len(d_dates))]
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
    hard = [a for a in last10 if is_hard(a)]
    days_since_ride = (today - d_dates[-1]).days if d_dates else 99
    hard_dates = [
        datetime.date.fromisoformat(a["date"]) for a in hard if a.get("date")
    ]
    days_since_hard = (today - max(hard_dates)).days if hard_dates else 99
    today_rides = [a for a in acts if a.get("date") == today.isoformat()]
    t_km = sum(d_dist(a) or 0 for a in today_rides)
    t_hr = max([num(a.get("avg_hr")) or 0 for a in today_rides] + [0])
    week_ago = (today - datetime.timedelta(days=7)).isoformat()
    rides_7d = [a for a in acts if a.get("date", "") >= week_ago]
    km_7d = sum(d_dist(a) or 0 for a in rides_7d)

    # ---- wellness facts (last 7d + latest) ----
    recent = dates[-7:]
    def col(key):
        return [num(daily[d].get(key)) for d in recent if num(daily[d].get(key)) is not None]

    hrv = col("hrv")
    rhr = col("resting_hr")
    hrv_mean = sum(hrv) / len(hrv) if hrv else None
    rhr_mean = sum(rhr) / len(rhr) if rhr else None
    latest_key = dates[-1]

    def fmt(v):
        if v is None:
            return "n/a"
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    def latest_num(key, back=4):
        # Today's entry is partial until Garmin finalizes it — scan back
        # for the most recent day that actually has a value.
        for d in reversed(dates[-back:]):
            v = num(daily[d].get(key))
            if v is not None:
                return v, d
        return None, latest_key

    readiness, r_date = latest_num("training_readiness")
    hrv_now, h_date = latest_num("hrv")
    rhr_now, rh_date = latest_num("resting_hr")
    stress_now, _s_date = latest_num("stress")
    bb_now, _b_date = latest_num("body_battery")
    sleep_now, sleep_date = None, latest_key
    for d in reversed(dates[-4:]):
        s = parse_sleep_hours(daily[d].get("sleep_hours"))
        if s is not None:
            sleep_now, sleep_date = s, d
            break
    sleep_3d = [parse_sleep_hours(daily[d].get("sleep_hours")) for d in dates[-3:]]
    sleep_3d = [s for s in sleep_3d if s is not None]
    sleep_debt = bool(sleep_3d) and (sum(sleep_3d) / len(sleep_3d) < 7.0)

    # ---- red flags ----
    flags = []
    if readiness is not None and readiness < 55:
        flags.append(f"Readiness {fmt(readiness)} (<55) on {r_date}")
    if hrv_now is not None and hrv_mean is not None and hrv_now < hrv_mean - 10:
        flags.append(f"HRV {hrv_now} is >10 below your 7d avg ({hrv_mean:.0f})")
    if rhr_now is not None and rhr_mean is not None and rhr_now >= rhr_mean + 5:
        flags.append(f"Resting HR {rhr_now} is +5 above your 7d avg ({rhr_mean:.0f})")
    if sleep_now is not None and sleep_now < 6.0:
        flags.append(f"Only {daily[sleep_date].get('sleep_hours')} sleep ({sleep_date})")
    today_long = t_km >= 60
    t_np = 0
    for _ta in today_rides:
        _n = np_by_id.get(str(_ta.get("id") or ""))
        if _n is not None and _n > t_np:
            t_np = _n
    today_hard = (t_hr >= 159 and t_km >= 30) or t_np >= 170
    if today_long or today_hard:
        flags.append(f"Today's ride was big ({t_km:.0f} km, avg HR {t_hr:.0f}) — body needs absorption time")
    last3 = [(today - datetime.timedelta(days=i)).isoformat() for i in range(3)]
    ridden_3 = sum(1 for d in last3 if any(a.get("date") == d for a in acts))
    if ridden_3 >= 3:
        flags.append("Rode 3 days straight — a day off protects the streak")

    # ---- verdict ----
    reasons = []
    if len(flags) >= 2 or (readiness is not None and readiness < 50):
        verdict = "rest"
        reasons = flags[:3] or ["Recovery signals say absorb, not add"]
    elif today_rides:
        if today_long or today_hard or len(flags) >= 1:
            verdict = "rest" if len(flags) >= 1 and (today_long or today_hard) else "easy"
            reasons = flags[:2] + [f"Today you rode {t_km:.0f} km — tomorrow stays gentle"] if verdict == "easy" else flags[:3]
        else:
            verdict = "easy"
            reasons = [f"Today was an easy {t_km:.0f} km — a recovery spin keeps legs turning over"]
    elif len(flags) == 1:
        verdict = "easy"
        reasons = flags[:1] + ["One yellow flag: keep it conversational"]
    else:
        verdict = "ride"
        reasons = [
            f"{len(acts) and 'Recovery is green (no flags)' or 'No flags'} — readiness {fmt(readiness)} ({r_date}), HRV {fmt(hrv_now)} ({h_date}), RHR {fmt(rhr_now)} ({rh_date})",
            f"{days_since_ride} day(s) since last ride ({d_dates[-1].isoformat() if d_dates else 'n/a'}), {days_since_hard} since last hard effort",
        ]

    # ---- ride prescription ----
    ride = None
    if verdict in ("ride", "easy"):
        if verdict == "easy":
            dist = max(12, round(avg_dist * 0.35))
            dur_h = dist / 22.0
            ride = {
                "title": "Recovery spin",
                "distance": f"{dist} km (≈{int(round(dur_h * 60))} min)",
                "duration_h": round(dur_h, 2),
                "warmup": "First 10 min dead easy, cadence 85–95",
                "main": f"Entirely Z1 (<{Z1_TOP} bpm), conversational — you should be able to hum",
                "variation": "6 × 1-min fast pedals (100+ rpm, stays Z1) with 4-min easy between — leg speed, zero load",
                "cooldown": "Last 5 min super easy + 5-min stretch off the bike",
                "pace": f"Cap HR at {Z1_TOP} bpm; ignore speed; RPE 2–3/10",
            }
        else:
            do_intervals = days_since_hard >= 3 and len(rides_7d) >= 2
            if do_intervals:
                ride = {
                    "title": "VO2 refresher (5 × 3 min)",
                    "distance": "≈40 km (≈85 min total)",
                    "duration_h": 1.4,
                    "warmup": "15 min Z1–Z2 + 3 × 30-s openers",
                    "main": f"5 × 3 min at {Z4_TOP}–{Z4_TOP + 6} bpm (high Z4/low Z5) with 3-min easy spins between",
                    "variation": "If legs feel great on rep 4, extend rep 5 to 4 min — never add a 6th rep",
                    "cooldown": "12–15 min Z1 + stretch",
                    "pace": f"Reps {Z4_TOP}–{Z4_TOP + 6} bpm; everything else <{Z2_TOP} bpm; RPE 8/10 on reps, 3/10 off",
                }
            else:
                dist = min(70, max(35, round(avg_dist / 5) * 5))
                dur_h = dist / 27.0
                ride = {
                    "title": "Happy endurance",
                    "distance": f"{dist} km (≈{int(round(dur_h * 60))} min)",
                    "duration_h": round(dur_h, 2),
                    "warmup": "First 15 min Z1–Z2, let HR settle",
                    "main": f"Steady Z2 ({Z1_TOP}–{Z2_TOP} bpm), nose-breathing; final 15 min may drift to Z3 ({Z2_TOP}–{Z3_TOP})",
                    "variation": "Progressive thirds: each third a touch brisker, capped at {cap} bpm".format(cap=Z2_TOP),
                    "cooldown": "Last 10 min Z1 + stretch",
                    "pace": f"Cap {Z2_TOP} bpm except the closing 15 min (cap {Z3_TOP}); RPE 4–5/10",
                }

    # ---- fueling (bananas / Fast&Up gels / Reload) ----
    H = ride["duration_h"] if ride else 0.0
    if ride:
        gels = max(0, round(H * 60 / 45) - 1)
        reload_during = max(1, round(H))
        fuel = {
            "pre": [
                "2–3 h before: regular meal (rice/roti + dal/eggs — normal food, nothing exotic)",
                "60–90 min before: 1 banana + 250 ml water",
                "1–2 h before: 500 ml water with 1 Fast&Up Reload",
            ],
            "during": [
                f"{reload_during} × 500–750 ml bottle(s) with Reload (1 serving per bottle, ~1 bottle/hr)"
            ]
            + ([f"{gels} × Fast&Up energy gel (~1 per 40–45 min after the first hour)"] if gels else ["Water + Reload is enough under ~75 min — no gel needed"])
            + (["1 banana mid-ride as solid backup on top of gels"] if H >= 2 else []),
            "post": [
                "Within 30–60 min: 1–2 bananas + a protein-rich meal",
                "500 ml water with 1 Reload within the hour",
            ],
        }
    else:
        fuel = {
            "pre": [],
            "during": [],
            "post": [
                "Eat normally; keep 1–2 bananas in the day as usual",
                "2–3 L water through the day + 1 Reload serving in the afternoon bottle",
                "Dinner with protein + carbs — recovery is built overnight",
            ],
        }

    # ---- sleep ----
    usual = usual_bedtime(daily, dates)
    target = 8.0 if (verdict != "rest" or sleep_debt) else 7.5
    bedtime = None
    if usual:
        try:
            t = datetime.datetime.strptime(usual, "%I:%M %p")
            mins = t.hour * 60 + t.minute
            if sleep_debt or (ride and ride.get("title", "").startswith("VO2")):
                mins -= 30
            h, m = divmod(mins % 1440, 60)
            bedtime = f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
        except Exception:
            bedtime = usual
    sleep = {
        "usual_bedtime": usual or "your usual time",
        "tonight": bedtime or "30 min earlier than usual" if (sleep_debt or verdict != "rest") else (usual or "your usual time"),
        "target": f"{target:.1f} h",
        "notes": [
            f"Target {target:.1f} h — " + ("you're running a small sleep debt" if sleep_debt else "protects tomorrow's output"),
            "Screens off 30 min before bed; cool, dark room",
        ],
    }

    # ---- rest-day plan ----
    rest_plan = None
    if verdict == "rest":
        rest_plan = [
            "No bike. 20–30 min easy walk (conversational pace, <5k steps extra) OR full couch — your call",
            "5-min mobility: calves, quads, hip flexors, thoracic opener",
            "If legs feel heavy: 10 min easy spin with zero resistance is allowed, HR <130",
            "Normal food + the hydration above; early night beats everything",
        ]

    briefing = {
        "for_date": tomorrow.isoformat(),
        "verdict": verdict,
        "verdict_label": {"ride": "Ride tomorrow", "easy": "Easy spin only", "rest": "Rest day"}[verdict]
        + (f" — {ride['title']}" if ride else ""),
        "reasons": reasons,
        "context": {
            "rides_7d": len(rides_7d),
            "km_7d": round(km_7d, 1),
            "avg_gap_days": round(avg_gap, 1),
            "days_since_ride": days_since_ride,
            "days_since_hard": days_since_hard,
            "today": ("rest" if not today_rides else f"{t_km:.0f} km, avg HR {t_hr:.0f}"),
            "flags": flags,
            "last10": [
                {"date": a.get("date"), "km": d_dist(a), "avg_hr": num(a.get("avg_hr"))} for a in last10
            ],
        },
        "ride": ride,
        "rest_plan": rest_plan,
        "fuel": fuel,
        "sleep": sleep,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": "rules-v1",
    }
    return briefing


def fallback(err):
    tomorrow = today_ist() + datetime.timedelta(days=1)
    return {
        "for_date": tomorrow.isoformat(),
        "verdict": "easy",
        "verdict_label": "Easy spin only (safe fallback)",
        "reasons": [f"Briefing engine hiccup ({err}) — defaulting to gentle. Data sync itself is unaffected."],
        "context": {"rides_7d": 0, "km_7d": 0, "avg_gap_days": 0, "days_since_ride": 0, "days_since_hard": 0, "today": "unknown", "flags": [], "last10": []},
        "ride": {
            "title": "Recovery spin",
            "distance": "12–18 km (≈30–40 min)",
            "duration_h": 0.6,
            "warmup": "First 10 min dead easy",
            "main": "Z1 (<139 bpm), conversational",
            "variation": "Skip intervals today",
            "cooldown": "5 min easy + stretch",
            "pace": "Cap 139 bpm; RPE 2–3/10",
        },
        "rest_plan": None,
        "fuel": {
            "pre": ["60–90 min before: 1 banana + 250 ml water"],
            "during": ["Water + 1 Reload bottle"],
            "post": ["1 banana + normal meal within the hour"],
        },
        "sleep": {"usual_bedtime": "your usual time", "tonight": "your usual time", "target": "7.5 h", "notes": ["Aim for 7.5 h"]},
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": "fallback",
    }


def render_md(b):
    L = [f"# Tomorrow's briefing — {b['for_date']} ({b['verdict_label']})", ""]
    L.append("## Verdict")
    for r in b["reasons"]:
        L.append(f"- {r}")
    L.append("")
    c = b["context"]
    L.append(f"_Last 7d: {c['rides_7d']} rides, {c['km_7d']} km · avg gap {c['avg_gap_days']}d · {c['days_since_ride']}d since ride, {c['days_since_hard']}d since hard · today: {c['today']}_")
    L.append("")
    if b["ride"]:
        r = b["ride"]
        L.append("## Ride plan")
        L.append(f"- **{r['title']}** — {r['distance']}")
        L.append(f"- Warmup: {r['warmup']}")
        L.append(f"- Main: {r['main']}")
        L.append(f"- Twist: {r['variation']}")
        L.append(f"- Cooldown: {r['cooldown']}")
        L.append(f"- Pace: {r['pace']}")
        L.append("")
    if b["rest_plan"]:
        L.append("## Rest plan")
        for x in b["rest_plan"]:
            L.append(f"- {x}")
        L.append("")
    L.append("## Fuel (bananas / Fast&Up gels / Reload)")
    for k in ("pre", "during", "post"):
        items = b["fuel"].get(k) or []
        if items:
            L.append(f"### {k.capitalize()}")
            for x in items:
                L.append(f"- {x}")
    L.append("")
    s = b["sleep"]
    L.append("## Sleep tonight")
    L.append(f"- Lights out: **{s['tonight']}** (usual {s['usual_bedtime']}) — target **{s['target']}**")
    for x in s["notes"]:
        L.append(f"- {x}")
    L.append("")
    L.append(f"_Generated {b['generated_at']} by {b['model']}_")
    return "\n".join(L)


def main():
    try:
        briefing = build()
    except Exception as e:
        import traceback

        traceback.print_exc()
        briefing = fallback(str(e)[:200])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(briefing, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_MD.write_text(render_md(briefing), encoding="utf-8")
    print(f"Wrote {OUT_JSON} verdict={briefing.get('verdict')}")


if __name__ == "__main__":
    main()
