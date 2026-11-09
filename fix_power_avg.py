#!/usr/bin/env python3
"""
Per-ride power numbers + FTP for the dashboard -> garmin/power_curves.json.

For every road_biking ride in garmin/data.json:
  - best average power over 10s / 30s / 60s / 5m / 20m / 1h (rolling, from the
    directPower stream)
  - whole-ride avg / normalized / max power (Garmin's own summary values first,
    stream-computed as a fallback)

Then picks the FTP the dashboard shows: Garmin's current cycling FTP when it is
recent, otherwise an estimate from the best 20-minute power of the last 6 weeks.

What was wrong before (curve_version 1):
  * Rolling windows that ran off the end of the ride were still counted, so a
    hard final 2 minutes became the "best 5 min", "best 20 min" AND "best 1 h"
    (e.g. 281/281/281 W on 2026-10-18). Windows must now be completely filled;
    a ride shorter than the window has no value for it.
  * Windows averaged samples, not time. Garmin records irregularly (and the
    details endpoint downsamples to 2000 points by default), so the stream is
    now resampled to 1 Hz before any averaging, with auto-pause gaps removed.
  * Garmin's summary numbers sit under get_activity()["summaryDTO"], but were
    read from the top level, so Garmin's own NP/avg were never used.
  * New rides only got NP/max a day late (first pass skipped unknown ids).

Run after sync_garmin.py:  python fix_power_avg.py
"""

import datetime
import json
import os
import pathlib

DATA_PATH = pathlib.Path("garmin/data.json")
PC_PATH = pathlib.Path("garmin/power_curves.json")

CURVE_VERSION = 2
WINDOWS = [10, 30, 60, 300, 1200, 3600]
MAXCHART = 20000          # ask for full-resolution samples (default is 2000)
FTP_FRESH_DAYS = 120      # a Garmin FTP older than this is treated as stale
FTP_LOOKBACK_DAYS = 42    # 20-min bests considered for the estimate
MAX_LEGACY_PER_RUN = 40   # older v1 curves upgraded per sync (bounds API calls)


# ---------------------------------------------------------------- pure math --

def _num(d, *keys):
    """First numeric value found (0 preserved — unlike `or` chains)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
    return None


def to_1hz(times_s, powers, hold_max=None):
    """Resample an irregular power stream to one value per second.

    Each sample holds until the next one. Gaps longer than `hold_max` are
    auto-pause / stops and are collapsed rather than filled, so a café stop
    neither adds zeros nor stretches a sample across ten minutes.
    """
    pairs = sorted(
        (float(t), float(p)) for t, p in zip(times_s, powers)
        if t is not None and p is not None
    )
    if not pairs:
        return []
    gaps = sorted(pairs[i + 1][0] - pairs[i][0] for i in range(len(pairs) - 1))
    gaps = [g for g in gaps if g > 0]
    med = gaps[len(gaps) // 2] if gaps else 1.0
    if hold_max is None:
        # Smart recording leaves gaps of several seconds during steady riding;
        # only longer silences are real stops.
        hold_max = max(15.0, 4 * med)
    out, carry = [], 0.0
    for i, (t, p) in enumerate(pairs):
        if i + 1 < len(pairs):
            dt = pairs[i + 1][0] - t
            if dt <= 0:
                continue  # duplicate timestamp: the later sample wins
            if dt > hold_max:
                dt = med
        else:
            dt = med
        carry += dt
        n = int(carry)
        carry -= n
        out.extend([p] * n)
    return out


def best_avg(series, window_s):
    """Best mean power over any *complete* window of `window_s` seconds (1 Hz series)."""
    n = len(series)
    if n < window_s or window_s <= 0:
        return None
    run = sum(series[:window_s])
    best = run
    for i in range(window_s, n):
        run += series[i] - series[i - window_s]
        if run > best:
            best = run
    return best / window_s


def normalized_power(series):
    """Coggan NP: 4th-power mean of 30 s rolling averages (1 Hz series)."""
    n = len(series)
    if n < 30:
        return None
    run = sum(series[:30])
    tot, cnt = (run / 30) ** 4, 1
    for i in range(30, n):
        run += series[i] - series[i - 30]
        tot += (run / 30) ** 4
        cnt += 1
    return (tot / cnt) ** 0.25


def power_stream(details):
    """(times_s, powers) from a get_activity_details() payload, or ([], [])."""
    if not isinstance(details, dict):
        return [], []
    descs = {m.get("key"): m.get("metricsIndex") for m in details.get("metricDescriptors") or []}
    p_idx, t_idx = descs.get("directPower"), descs.get("directTimestamp")
    if not isinstance(p_idx, int) or not isinstance(t_idx, int):
        return [], []
    times, powers = [], []
    for m in details.get("activityDetailMetrics") or []:
        arr = m.get("metrics") or []
        if p_idx >= len(arr) or t_idx >= len(arr):
            continue
        p, t = arr[p_idx], arr[t_idx]
        if p is None or t is None:
            continue
        times.append(t / 1000.0)
        powers.append(float(p))
    return times, powers


def curve_from_stream(times_s, powers):
    """Rolling bests + stream avg/NP/max, or None when there is no power data."""
    series = to_1hz(times_s, powers)
    if not series:
        return None
    bests = {f"{w}s": (round(b) if (b := best_avg(series, w)) is not None else None) for w in WINDOWS}
    np_ = normalized_power(series)
    return {
        **bests,
        "streamAvg": round(sum(series) / len(series), 1),
        "streamNP": round(np_, 1) if np_ is not None else None,
        "streamMax": round(max(series)),
        "seconds": len(series),
    }


def rolling_ftp_estimate(curves, as_of):
    """FTP estimate as of a date, from rides in the FTP_LOOKBACK_DAYS before it.

    95% of the best 20-min power, but never below the best 60-min power
    (FTP is by definition ~ the power you can hold for an hour). Only
    corrected (v2) curves count. Returns None when no ride qualifies.
    """
    lo = (as_of - datetime.timedelta(days=FTP_LOOKBACK_DAYS)).isoformat()
    hi = as_of.isoformat()
    b20 = b20_date = b60 = None
    for c in curves.values():
        if not isinstance(c, dict) or c.get("v") != CURVE_VERSION:
            continue
        d = str(c.get("date", ""))
        if not (lo <= d <= hi):
            continue
        v20, v60 = c.get("1200s"), c.get("3600s")
        if isinstance(v20, (int, float)) and (b20 is None or v20 > b20):
            b20, b20_date = v20, d
        if isinstance(v60, (int, float)) and (b60 is None or v60 > b60):
            b60 = v60
    if not b20:
        return None
    value = max(round(0.95 * b20), round(b60) if b60 else 0)
    return {"value": value, "from_20m": b20, "date": b20_date, "best_60m": b60}


def ftp_history(curves, dates):
    """[{date, value}] — the rolling estimate as it stood on each date."""
    out = []
    for d in sorted(dates):
        try:
            est = rolling_ftp_estimate(curves, datetime.date.fromisoformat(d))
        except ValueError:
            continue
        out.append({"date": d, "value": est["value"] if est else None})
    return out


def choose_ftp(garmin_ftp, curves, today, prev=None):
    """Return (ftp, status, source, estimate) for the dashboard / AI prompt.

    Re-evaluated on every sync: a fresh Garmin FTP (e.g. after an FTP test or
    Garmin's auto-detect) wins; otherwise the rolling estimate tracks the
    rider's latest 6 weeks of rides, so it rises after strong efforts and
    drifts down if there have been none.
    """
    estimate = rolling_ftp_estimate(curves, today)
    b20 = estimate["from_20m"] if estimate else None
    b20_date = estimate["date"] if estimate else None

    g_val = garmin_ftp.get("value") if isinstance(garmin_ftp, dict) else None
    g_date = garmin_ftp.get("date") if isinstance(garmin_ftp, dict) else None
    age = None
    if g_date:
        try:
            age = (today - datetime.date.fromisoformat(str(g_date)[:10])).days
        except ValueError:
            age = None
    fresh = g_val and age is not None and age <= FTP_FRESH_DAYS and garmin_ftp.get("is_stale") is not True

    if fresh:
        return g_val, f"Garmin FTP, set {g_date}", "garmin", estimate
    if estimate:
        why = (f"Garmin FTP {g_val} W from {g_date} is stale" if g_val
               else "Garmin has no cycling FTP on file")
        return (estimate["value"],
                f"Estimated from the last {FTP_LOOKBACK_DAYS // 7} weeks: 95% of best 20-min power "
                f"({b20} W on {b20_date}) — {why}. Updates after every ride; normal rides aren't "
                "all-out tests, so true FTP is likely a bit higher.",
                "estimate", estimate)
    if g_val:
        return g_val, f"Garmin FTP from {g_date or 'unknown date'} — stale, retest recommended", "garmin-stale", None
    if prev and prev.get("ftp"):
        return prev["ftp"], prev.get("ftp_status") or "previous value", prev.get("ftp_source") or "previous", None
    return None, "Unknown — no Garmin FTP and no 20-min power yet", "none", None


# ------------------------------------------------------------- Garmin I/O --

def _login():
    from garminconnect import Garmin
    tokenstore = os.path.expanduser("~/.garminconnect")
    email, password = os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD")
    # Pass credentials when available so garminconnect>=0.3.6 can self-heal from
    # expired/poisoned cached tokens ("Failed to retrieve social profile").
    g = Garmin(email=email, password=password) if email and password else Garmin()
    g.login(tokenstore)
    return g


def _today_ist():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return datetime.date.today()


def refresh_ride(g, aid, date, raw):
    """Recompute one ride's curve entry from Garmin (summary + full-res stream)."""
    act = g.get_activity(aid) or {}
    summ = act.get("summaryDTO") if isinstance(act.get("summaryDTO"), dict) else act
    details = g.get_activity_details(aid, maxchart=MAXCHART)
    times, powers = power_stream(details)
    stream = curve_from_stream(times, powers)
    raw = raw or {}

    avg = _num(summ, "averagePower", "avgPower") or _num(raw, "averagePower")
    np_ = _num(summ, "normalizedPower", "normPower") or _num(raw, "normalizedPower")
    mx = _num(summ, "maxPower") or _num(raw, "maxPower")
    np_src = "garmin" if np_ is not None else None
    if stream:
        avg = avg if avg is not None else stream["streamAvg"]
        mx = mx if mx is not None else stream["streamMax"]
        if np_ is None and stream["streamNP"] is not None:
            np_, np_src = stream["streamNP"], "computed"
        elif np_ is not None and stream["streamNP"] is not None and abs(stream["streamNP"] - np_) > 15:
            print(f"{aid}: note — Garmin NP {np_} vs stream NP {stream['streamNP']}, keeping Garmin's")

    if not date:
        date = str(summ.get("startTimeLocal") or act.get("startTimeLocal") or "")[:10] or None
    new = {"date": date, "v": CURVE_VERSION}
    if stream:
        new.update({k: stream[k] for k in [f"{w}s" for w in WINDOWS]})
    else:
        new["noPower"] = True
    new.update({
        "avgPower": round(avg, 1) if avg is not None else None,
        "normalizedPower": round(np_, 1) if np_ is not None else None,
        "maxPower": round(mx) if mx is not None else None,
        "npSource": np_src,
    })
    return new


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    rides = [a for a in data.get("activities", []) if a.get("sport") == "road_biking"]
    try:
        pc = json.loads(PC_PATH.read_text(encoding="utf-8"))
    except Exception:
        pc = {}
    pc.setdefault("curves", {})
    print(f"Found {len(rides)} cycling activities in data.json")

    # Rides in the sync window, then older rides still cached with the buggy
    # v1 math (so the 6-week FTP lookback and history use correct numbers).
    todo = []
    for a in rides:
        aid = str(a.get("id") or (a.get("_raw") or {}).get("activityId") or "")
        if aid and aid != "None":
            todo.append((aid, a.get("date"), a.get("_raw") or {}))
    in_window = {t[0] for t in todo}
    legacy = [(aid, c.get("date"), {}) for aid, c in pc["curves"].items()
              if aid not in in_window and isinstance(c, dict) and c.get("v") != CURVE_VERSION]
    if legacy:
        print(f"Upgrading {min(len(legacy), MAX_LEGACY_PER_RUN)} of {len(legacy)} older rides to curve v{CURVE_VERSION}")
    todo += sorted(legacy, key=lambda t: str(t[1] or ""), reverse=True)[:MAX_LEGACY_PER_RUN]

    g = None
    for aid, date, raw in todo:
        entry = pc["curves"].get(aid) or {}
        done = entry.get("v") == CURVE_VERSION and (
            entry.get("noPower") or all(entry.get(k) is not None for k in ("avgPower", "normalizedPower", "maxPower"))
        )
        if done:
            continue
        if g is None:
            g = _login()
        try:
            new = refresh_ride(g, aid, date, raw)
            pc["curves"][aid] = new
            print(aid, new.get("date"), {k: new.get(k) for k in ("10s", "300s", "1200s", "3600s", "avgPower", "normalizedPower", "maxPower", "npSource")})
        except Exception as e:
            print(f"{aid}: failed ({e}) — keeping previous entry")

    today = _today_ist()
    profile_ftp = (data.get("profile") or {}).get("ftp")
    ftp, status, source, estimate = choose_ftp(profile_ftp, pc["curves"], today, prev=pc)
    pc.update({
        "computed_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "curve_version": CURVE_VERSION,
        "ftp": ftp,
        "ftp_status": status,
        "ftp_source": source,
        "ftp_garmin": profile_ftp,
        "ftp_estimate": estimate,
        # Rolling estimate as it stood on each day of the sync window -> FTP trend chart
        "ftp_history": ftp_history(pc["curves"], list((data.get("daily") or {}).keys())),
        "notes": ("Rolling bests from Garmin's directPower stream resampled to 1 Hz (pauses removed); "
                  "a window only counts when completely filled. 10s=neuromuscular, 30-60s=anaerobic, "
                  "5m=VO2, 20m≈FTP proxy, 1h=endurance. Entries without \"v\": 2 are legacy values."),
    })
    PC_PATH.write_text(json.dumps(pc, indent=2), encoding="utf-8")
    print(f"updated {PC_PATH} — FTP {ftp} W ({source}): {status}")


if __name__ == "__main__":
    main()
