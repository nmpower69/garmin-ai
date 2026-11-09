#!/usr/bin/env python3
"""
Garmin to Markdown sync — read-only.

Pulls your recent Garmin workouts + daily wellness (sleep, HRV, resting HR,
body battery, stress, steps, training readiness) and saves:

  ./garmin/data.json        — structured JSON for all days + activities
  ./garmin/daily/YYYY-MM-DD.md — one wellness note per day (plain English)
  ./garmin/activities/YYYY-MM-DD-<id>-<sport>.md — one note per workout

Security:
  - Password is typed via hidden prompt (getpass), never saved, never printed.
  - Login tokens are saved privately to ~/.garminconnect/garmin_tokens.json
    with folder 0700 / file 0600 permissions where supported.
  - Tokens and passwords are never shown in terminal, markdown, or data.json.
  - Read-only: this script only calls GET methods, never writes to Garmin.

Usage (run from this folder):
  py sync_garmin.py --login
  py sync_garmin.py --days 3 --dry-run
  py sync_garmin.py --days 3 --sink files --out ./garmin
  py sync_garmin.py --days 7 --sink files --out ./garmin

Requires: pip install -r requirements.txt  (garminconnect + curl_cffi)
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path
import stat
import time
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = None

def _now_ist():
    try:
        return datetime.now(IST) if IST else datetime.now()
    except:
        return datetime.now()

def _today_ist():
    try:
        return _now_ist().date()
    except:
        return date.today()

# Keep imports lazy so --help works even before deps are installed
TOKENSTORE_DEFAULT = "~/.garminconnect"

# Helper to restrict token permissions (best effort on Windows)
def _secure_path(path: Path, is_dir: bool = False):
    try:
        if os.name != "nt":
            mode = 0o700 if is_dir else 0o600
            os.chmod(path, mode)
        else:
            # On Windows, Python chmod only sets read-only flag — not real ACLs.
            # Try icacls to restrict to current user only (best effort, may require no admin).
            try:
                import subprocess, getpass
                user = getpass.getuser()
                # Remove inherited permissions and grant only current user full control
                # This is best-effort; if it fails, warn but don't crash (token still works, just less private).
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"], capture_output=True, timeout=5)
            except Exception:
                pass
            # Also try chmod for read-only flag
            try:
                os.chmod(path, 0o600 if not is_dir else 0o700)
            except:
                pass
    except Exception:
        pass

def _ensure_tokenstore_dir(tokenstore: str) -> Path:
    p = Path(tokenstore).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    _secure_path(p, is_dir=True)
    # Also secure token file if exists
    token_file = p / "garmin_tokens.json"
    if token_file.exists():
        _secure_path(token_file, is_dir=False)
    return p

def _get_client(tokenstore: str):
    """Return Garmin client logged in via stored tokens if possible."""
    try:
        from garminconnect import Garmin
    except ImportError:
        print("Missing dependency: garminconnect. Run: py -m pip install -r requirements.txt")
        sys.exit(1)
    # Try token-only login first (no credentials needed if tokens valid)
    try:
        g = Garmin()
        g.login(tokenstore)
        return g
    except Exception:
        return None

def do_login(tokenstore: str):
    """One-time Garmin login. Asks for email + hidden password, handles MFA."""
    try:
        from garminconnect import Garmin, GarminConnectAuthenticationError
    except ImportError:
        print("Missing dependency: run py -m pip install -r requirements.txt first")
        sys.exit(1)

    _ensure_tokenstore_dir(tokenstore)
    print(f"Garmin login — tokens will be saved to: {tokenstore}")
    print("This is read-only. Nothing will be written to your Garmin account.")
    print()

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    # Hidden prompt — password never echoes, never saved
    password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password (hidden, won't show): ")

    # Prompt for MFA if Garmin asks
    def _prompt_mfa():
        return input("Garmin 2FA code (check email/app): ").strip()

    try:
        client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
        # Clear local password reference immediately after use
        # (Garmin object also clears it internally after success)
        client.login(tokenstore)
        password = None  # noqa
        # Secure token file after login
        _ensure_tokenstore_dir(tokenstore)
        token_file = Path(tokenstore).expanduser() / "garmin_tokens.json"
        if token_file.exists():
            _secure_path(token_file, is_dir=False)
            print(f"\nLogin successful! Token saved privately to {token_file}")
            print("Next time you won't need to log in again — tokens auto-refresh.")
        else:
            print("\nLogin succeeded but token file not found — you may need to log in again next time.")
        print("Your password was not saved anywhere.")
        return True
    except Exception as e:
        # Never print password or token contents
        msg = str(e)
        # Sanitize any accidental token leakage
        if "password" in msg.lower():
            msg = "Authentication failed"
        print(f"\nLogin failed: {msg}")
        print("Tip: double-check email/password and 2FA code. If it persists, wait a few minutes (Garmin rate limit).")
        return False

RATE_LIMIT_HITS = 0   # calls that stayed rate-limited after retries (stops the backfill)
TRANSIENT_ERRORS = 0  # rate limits, timeouts, connection/5xx errors: the day is retried next run

def _is_rate_limited(e):
    s = f"{type(e).__name__} {e}".lower()
    return "429" in s or "too many" in s or "toomanyrequests" in s or "rate limit" in s

def _is_transient(e):
    s = f"{type(e).__name__} {e}".lower()
    return _is_rate_limited(e) or any(t in s for t in (
        "timeout", "timed out", "connection", "temporarily", " 500", " 502", " 503", " 504"))

def _safe_call(label, func, *args, **kwargs):
    """Call a Garmin API; return (result, None) or (None, error text).

    On HTTP 429 (Garmin rate limit) wait and retry twice before giving up —
    a long history backfill makes hundreds of calls.
    """
    global RATE_LIMIT_HITS, TRANSIENT_ERRORS
    for attempt in range(3):
        try:
            return func(*args, **kwargs), None
        except Exception as e:
            if _is_rate_limited(e) and attempt < 2:
                wait = 20 * (attempt + 1)
                print(f"  rate-limited on {label} — waiting {wait}s and retrying")
                time.sleep(wait)
                continue
            if _is_rate_limited(e):
                RATE_LIMIT_HITS += 1
            if _is_transient(e):
                TRANSIENT_ERRORS += 1
            return None, f"{label}: {e}"

def _format_duration(seconds):
    if seconds is None:
        return "N/A"
    try:
        s = int(seconds)
        h = s // 3600
        m = (s % 3600) // 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return str(seconds)

def _extract_sleep(sleep_data):
    if not sleep_data or not isinstance(sleep_data, dict):
        return {"hours": "N/A", "score": "N/A", "deep": "N/A", "light": "N/A", "rem": "N/A", "awake": "N/A", "raw": None}
    dto = sleep_data.get("dailySleepDTO") or sleep_data
    # Try common fields
    seconds = dto.get("sleepTimeSeconds")
    if seconds is None:
        seconds = dto.get("sleepTimeInSeconds")
    score = dto.get("sleepScores") or dto.get("sleepScore")
    # Flatten score dict to string like "64 (FAIR)" or just value
    if isinstance(score, dict):
        # Handle nested overall/value
        val = score.get("overall") or score.get("value") or score.get("score")
        qual = score.get("qualifierKey") or score.get("qualifier")
        if isinstance(val, dict):
            qual = val.get("qualifierKey") or qual
            val = val.get("value") or val.get("overall") or val
        if val is not None and qual:
            score = f"{val} ({qual})"
        elif val is not None:
            score = val
        elif qual:
            score = qual
        else:
            score = str(score)
    elif isinstance(score, list) and score:
        first = score[0]
        if isinstance(first, dict):
            val = first.get("overall") or first.get("value") or first
            qual = first.get("qualifierKey") or ""
            if isinstance(val, dict):
                qual = val.get("qualifierKey") or qual
                val = val.get("value") or val
            score = f"{val} ({qual})" if qual and val else (val if val is not None else str(first))
        else:
            score = first
    # Sleep stages if present
    deep = dto.get("deepSleepSeconds")
    light = dto.get("lightSleepSeconds")
    rem = dto.get("remSleepSeconds")
    awake = dto.get("awakeSleepSeconds")
    return {
        "hours": _format_duration(seconds) if seconds else "N/A",
        "seconds": seconds,
        "score": score if score is not None else "N/A",
        "deep": _format_duration(deep) if deep else "N/A",
        "light": _format_duration(light) if light else "N/A",
        "rem": _format_duration(rem) if rem else "N/A",
        "awake": _format_duration(awake) if awake else "N/A",
        "raw": dto,
    }

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _extract_hrv(hrv_data):
    """Last night's average HRV (ms) — the value Garmin shows as 'last night'.

    Never substitutes the weekly average: on nights without an HRV reading the
    day is "N/A" instead of silently showing a 7-day mean labelled as last night.
    """
    if not hrv_data or not isinstance(hrv_data, dict):
        return "N/A"
    summ = hrv_data.get("hrvSummary")
    if isinstance(summ, list):
        summ = summ[0] if summ else None
    if isinstance(summ, dict) and _is_num(summ.get("lastNightAvg")):
        return summ["lastNightAvg"]
    if _is_num(hrv_data.get("lastNightAvg")):
        return hrv_data["lastNightAvg"]
    return "N/A"

def _extract_body_battery(stats, bb_data):
    """Highest Body Battery level of the day (0-100).

    get_body_battery() returns {charged, drained, bodyBatteryValuesArray, ...} —
    it has no "highest" field, so the old code fell through to `charged` (how
    many points were *gained*), which is not a level. The daily summary carries
    the real bodyBatteryHighestValue; the level array is the fallback.
    """
    if isinstance(stats, dict) and _is_num(stats.get("bodyBatteryHighestValue")):
        return stats["bodyBatteryHighestValue"]
    try:
        entries = bb_data if isinstance(bb_data, list) else [bb_data]
        levels = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            for pair in e.get("bodyBatteryValuesArray") or []:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2 and _is_num(pair[1]):
                    levels.append(pair[1])
        if levels:
            return max(levels)
    except Exception:
        pass
    return "N/A"

def _extract_stress(stress_data):
    if not stress_data or not isinstance(stress_data, dict):
        return "N/A"
    for k in ["overallStressLevel", "avgStressLevel", "averageStressLevel", "stressLevel", "value"]:
        v = stress_data.get(k)
        # Garmin uses -1/-2 for "not enough data" — that is not a stress level
        if _is_num(v) and v >= 0:
            return v
    # Sometimes stress in list
    if "stressValuesArray" in stress_data and stress_data["stressValuesArray"]:
        try:
            vals = [v for v in stress_data["stressValuesArray"] if isinstance(v, int) and v >= 0]
            if vals:
                return round(sum(vals) / len(vals))
        except Exception:
            pass
    return "N/A"

def _extract_rhr(hr_data, rhr_data):
    # Prefer rhr_data (get_rhr_day), then hr_data
    if rhr_data and isinstance(rhr_data, dict):
        # rhr_data often contains metrics list
        try:
            # Look for values like restingHeartRate
            if "restingHeartRate" in rhr_data and rhr_data["restingHeartRate"] not in (None, ""):
                return rhr_data["restingHeartRate"]
            if "allMetrics" in rhr_data and isinstance(rhr_data["allMetrics"], dict):
                metrics = rhr_data["allMetrics"].get("metricsMap") or rhr_data["allMetrics"]
                if isinstance(metrics, dict):
                    for k, v in metrics.items():
                        if "resting" in k.lower() and isinstance(v, (int, float)):
                            return v
            # Sometimes list of stats
            if "restingHeartRate" in str(rhr_data):
                # brute force search
                for v in rhr_data.values():
                    if isinstance(v, list) and v:
                        for item in v:
                            if isinstance(item, dict) and "restingHeartRate" in item:
                                return item["restingHeartRate"]
        except Exception:
            pass
    if hr_data and isinstance(hr_data, dict):
        for k in ["restingHeartRate", "restingHr", "rhr"]:
            if k in hr_data and hr_data[k] not in (None, ""):
                return hr_data[k]
    return "N/A"

def _extract_training_readiness(tr_data):
    """Morning Training Readiness score (0-100).

    Garmin returns several snapshots per day (wake-up, after each workout, ...)
    in no guaranteed order, so taking element [0] gave a different kind of
    reading from day to day. Prefer the wake-up snapshot (what the Morning
    Report shows); otherwise the most recent snapshot by timestamp.
    """
    if not tr_data:
        return "N/A"
    try:
        items = tr_data if isinstance(tr_data, list) else [tr_data]
        items = [i for i in items if isinstance(i, dict) and _is_num(i.get("score"))]
        if not items:
            return "N/A"
        morning = [i for i in items if i.get("inputContext") == "AFTER_WAKEUP_RESET"]
        if morning:
            return morning[0]["score"]
        items.sort(key=lambda i: str(i.get("timestampLocal") or i.get("timestamp") or ""))
        return items[-1]["score"]
    except Exception:
        pass
    return "N/A"

def _extract_vo2max(mm_data):
    """VO2 max from Garmin's max-metrics ("maxmet") payload.

    Shape: [{"generic": {...running/walking...}, "cycling": {...}, ...}] where
    each block has vo2MaxPreciseValue / vo2MaxValue / calendarDate. A rider who
    only cycles has generic=None and the number lives under "cycling" — which
    is why reading the generic value alone always looked like "null".
    Returns {"cycling": {"value", "date"} | None, "running": ... | None}.
    """
    out = {"cycling": None, "running": None}
    entries = mm_data if isinstance(mm_data, list) else [mm_data]
    for e in entries:
        if not isinstance(e, dict):
            continue
        for key, label in (("cycling", "cycling"), ("generic", "running")):
            blk = e.get(key)
            if not isinstance(blk, dict):
                continue
            v = blk.get("vo2MaxPreciseValue")
            if not _is_num(v) or v <= 0:
                v = blk.get("vo2MaxValue")
            if _is_num(v) and v > 0:
                prev = out[label]
                date_ = blk.get("calendarDate")
                if prev is None or str(date_ or "") >= str(prev.get("date") or ""):
                    out[label] = {"value": round(float(v), 1), "date": date_}
    return out

def _extract_ftp(ftp_data):
    """Latest cycling FTP from /biometric-service/biometric/latestFunctionalThresholdPower/CYCLING."""
    entries = ftp_data if isinstance(ftp_data, list) else [ftp_data]
    best = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        v = e.get("functionalThresholdPower")
        if not _is_num(v):
            v = e.get("value")
        if _is_num(v) and v > 0:
            cand = {"value": int(round(v)), "date": e.get("calendarDate") or e.get("updatedDate"),
                    "is_stale": e.get("isStale")}
            if best is None or str(cand["date"] or "") >= str(best["date"] or ""):
                best = cand
    return best

def _extract_hr_zones(zones_data):
    """Cycling (else default) heart-rate zone floors from get_heart_rate_zones()."""
    if not isinstance(zones_data, list):
        return None
    by_sport = {str(z.get("sport") or "").upper(): z for z in zones_data if isinstance(z, dict)}
    z = by_sport.get("CYCLING") or by_sport.get("DEFAULT") or (next(iter(by_sport.values())) if by_sport else None)
    if not z:
        return None
    floors = [z.get(f"zone{i}Floor") for i in range(1, 6)]
    if not all(_is_num(f) for f in floors):
        return None
    return {
        "sport": z.get("sport"),
        "method": z.get("trainingMethod"),
        "floors": [int(f) for f in floors],  # Z1..Z5 lower bounds (bpm)
        "max_hr": z.get("maxHeartRateUsed"),
        "lthr": z.get("lactateThresholdHeartRateUsed"),
        "resting_hr": z.get("restingHeartRateUsed"),
    }

def fetch_profile(client, today_iso):
    """Slow-moving physiology: latest VO2 max, cycling FTP, HR zones. One call each."""
    profile = {"vo2max": {"cycling": None, "running": None}, "ftp": None, "hr_zones": None}
    if hasattr(client, "get_max_metrics_latest"):
        mm, err = _safe_call("vo2maxLatest", client.get_max_metrics_latest, today_iso)
        if mm:
            profile["vo2max"] = _extract_vo2max(mm)
        elif err:
            print(f"  Note: {err}")
    if hasattr(client, "get_cycling_ftp"):
        ftp, err = _safe_call("cyclingFtp", client.get_cycling_ftp)
        profile["ftp"] = _extract_ftp(ftp)
        if err:
            print(f"  Note: {err}")
    if hasattr(client, "get_heart_rate_zones"):
        hz, err = _safe_call("hrZones", client.get_heart_rate_zones)
        profile["hr_zones"] = _extract_hr_zones(hz)
        if err:
            print(f"  Note: {err}")
    return profile

REFETCH_RECENT_DAYS = 7  # older days are final in Garmin; reuse them from the last sync

def month_window_start(today, months):
    """First day of the calendar month `months - 1` months before `today`'s month.

    months=2 on 2026-11-09 -> 2026-10-01: the current month so far plus the
    whole previous month, so the dashboard can show calendar months and
    compare this month with last month.
    """
    y, m = today.year, today.month - (months - 1)
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)

MAX_BACKFILL_DAYS = 120   # older missing days fetched per run (~10 calls each); the rest next run
RATE_LIMIT_STOP = 3       # stop backfilling after this many calls stay rate-limited

def _reusable_day(prev):
    """True if a stored day is complete and in the current format (no refetch needed)."""
    if not isinstance(prev, dict) or "vo2max_cycling" not in prev:
        return False  # written by the old sync format
    ok = prev.get("_sync_ok")
    if ok is None:  # stored before the flag existed: trust it if the daily summary had data
        ok = _is_num(prev.get("steps"))
    return bool(ok)

def fetch_days(client, days: int = None, start_date: date = None, existing: dict = None):
    """Make sure every day from `start_date` (or the last `days` days) to today is synced.

    data.json is a growing history, not a rolling window: days already stored
    (in the current format) are kept as they are and never dropped, the last
    REFETCH_RECENT_DAYS are always refreshed, and any missing day is fetched —
    newest first, at most MAX_BACKFILL_DAYS per run, so a long backfill
    finishes over a few runs instead of hitting Garmin's rate limit.
    Previously each sync overwrote data.json with only its window, which is
    why early-October rides (and all of Aug/Sep) disappeared.
    """
    today = _today_ist()
    if start_date is None:
        start_date = today - timedelta(days=(days or 7) - 1)
    n = (today - start_date).days + 1
    dates = [(start_date + timedelta(days=i)).isoformat() for i in range(n)]  # oldest first

    # For activities, use range query
    start = dates[0]
    end = dates[-1]
    reuse_before = (today - timedelta(days=REFETCH_RECENT_DAYS)).isoformat()
    old_daily = (existing or {}).get("daily") or {}

    recent = sorted((d for d in dates if d >= reuse_before), reverse=True)
    missing = sorted((d for d in dates if d < reuse_before and not _reusable_day(old_daily.get(d))), reverse=True)
    todo = recent + missing[:MAX_BACKFILL_DAYS]
    deferred = missing[MAX_BACKFILL_DAYS:]
    reused = len(dates) - len(recent) - len(missing)

    print(f"Syncing {start} to {end}: {len(recent)} recent day(s) to refresh, {len(missing)} missing day(s) to backfill, "
          f"{reused} already stored ... (IST: {_now_ist().strftime('%Y-%m-%d %H:%M %Z')})")
    daily = {}  # days fetched in this run
    for cdate in todo:
        if cdate < reuse_before and RATE_LIMIT_HITS >= RATE_LIMIT_STOP:
            deferred.append(cdate)  # Garmin is throttling us: leave the rest for the next run
            continue
        # Call each API safely so one failing doesn't break the whole day
        transient_before = TRANSIENT_ERRORS
        stats, stats_err = _safe_call("stats", client.get_user_summary, cdate)
        hr, _ = _safe_call("heart", client.get_heart_rates, cdate)
        sleep, _ = _safe_call("sleep", client.get_sleep_data, cdate)
        hrv, _ = _safe_call("hrv", client.get_hrv_data, cdate)
        # body battery: try range single day
        bb, _ = _safe_call("bodyBattery", client.get_body_battery, cdate)
        stress, _ = _safe_call("stress", client.get_stress_data, cdate)
        if stress is None or stress == "N/A":
            stress2, _ = _safe_call("allDayStress", client.get_all_day_stress, cdate)
            if stress2:
                stress = stress2
        rhr, _ = _safe_call("rhr", client.get_rhr_day, cdate)
        tr, _ = _safe_call("trainingReadiness", client.get_training_readiness, cdate)
        # VO2 max is only re-estimated on days with a qualifying workout, so most
        # days come back empty — the dashboard connects the dots between updates.
        mm, _ = _safe_call("vo2max", client.get_max_metrics, cdate)

        steps = "N/A"
        if stats and isinstance(stats, dict):
            steps = stats.get("totalSteps") or stats.get("totalStepsDisplay") or "N/A"
            # fallback: steps from stats
            if steps == "N/A" and "dailyStepGoal" in stats:
                steps = stats.get("totalSteps", "N/A")

        sleep_info = _extract_sleep(sleep)
        hrv_val = _extract_hrv(hrv) if hrv else "N/A"
        bb_val = _extract_body_battery(stats, bb)
        stress_val = _extract_stress(stress)
        rhr_val = _extract_rhr(hr, rhr)
        tr_val = _extract_training_readiness(tr)
        vo2 = _extract_vo2max(mm)
        # maxmet for a single day can echo an older estimate — only count it
        # for this day if Garmin dated it this day.
        vo2_cyc = vo2["cycling"]["value"] if vo2["cycling"] and vo2["cycling"].get("date") in (None, cdate) else None
        vo2_run = vo2["running"]["value"] if vo2["running"] and vo2["running"].get("date") in (None, cdate) else None

        # Calories, distance from stats
        calories = "N/A"
        distance_km = "N/A"
        if stats and isinstance(stats, dict):
            c = stats.get("totalKilocalories") or stats.get("activeKilocalories") or stats.get("bmrKilocalories")
            if c is not None:
                try:
                    calories = int(c)
                except Exception:
                    calories = c
            dist_m = stats.get("totalDistanceMeters")
            if dist_m is not None:
                try:
                    distance_km = round(float(dist_m) / 1000, 2)
                except Exception:
                    distance_km = dist_m

        # Keep only what is actually consumed + user-approved future fields.
        # Flat fields already cover hrv/stress/bodyBattery/rhr/trainingReadiness.
        # _raw therefore keeps ONLY minimal sleep DTO (9 keys ~296 B) and intensity mins are promoted.
        moderate_min = None
        vigorous_min = None
        if isinstance(stats, dict):
            moderate_min = stats.get("moderateIntensityMinutes")
            vigorous_min = stats.get("vigorousIntensityMinutes")
        # Minimal sleep DTO — keep only fields read by briefing.py + dashboard
        minimal_sleep = None
        if isinstance(sleep, dict):
            dto = sleep.get("dailySleepDTO") or {}
            if isinstance(dto, dict):
                keep_sleep = ["sleepStartTimestampLocal","sleepStartTimestampGMT","sleepEndTimestampLocal","sleepEndTimestampGMT","sleepTimeSeconds","deepSleepSeconds","lightSleepSeconds","remSleepSeconds","awakeSleepSeconds"]
                minimal_sleep = {k: dto.get(k) for k in keep_sleep if k in dto}
                # preserve as same shape so consumers keep working: _raw.sleep.dailySleepDTO
                minimal_sleep = {"dailySleepDTO": minimal_sleep} if minimal_sleep else None
        daily[cdate] = {
            "date": cdate,
            "steps": steps,
            "calories": calories,
            "distance_km": distance_km,
            "sleep_hours": sleep_info["hours"],
            "sleep_score": sleep_info["score"],
            "sleep_deep": sleep_info["deep"],
            "sleep_light": sleep_info["light"],
            "sleep_rem": sleep_info["rem"],
            "sleep_awake": sleep_info["awake"],
            "hrv": hrv_val,
            "resting_hr": rhr_val,
            "body_battery": bb_val,
            "stress": stress_val,
            "training_readiness": tr_val,
            "vo2max_cycling": vo2_cyc,
            "vo2max_running": vo2_run,
            "moderate_minutes": moderate_min if isinstance(moderate_min, (int, float)) else None,
            "vigorous_minutes": vigorous_min if isinstance(vigorous_min, (int, float)) else None,
            # False if any call that day hit a temporary error (rate limit, timeout,
            # 5xx) or the daily summary failed -> the whole day is refetched next run
            "_sync_ok": stats_err is None and TRANSIENT_ERRORS == transient_before,
            "_raw": {
                "sleep": minimal_sleep,
            } if minimal_sleep else {"sleep": None},
        }
        # Progress dot
        print(f"  {cdate}: steps={steps} hrv={hrv_val} rhr={rhr_val} bb={bb_val} stress={stress_val} readiness={tr_val} vo2max(cyc/run)={vo2_cyc}/{vo2_run} sleep={sleep_info['hours']} ({sleep_info['score']})")

    # Activities
    activities = []
    # Try date-range method first, fallback to recent list
    acts, err = _safe_call("activities_by_date", client.get_activities_by_date, start, end)
    # Only the full date-range list is authoritative; the fallback below is just
    # the latest 100, so with it stored rides are merged, never dropped.
    acts_ok = acts is not None and not err
    if acts is None or err:
        print(f"  Note: get_activities_by_date failed ({err}), trying get_activities(0, 20)...")
        acts2, err2 = _safe_call("activities", client.get_activities, 0, 100)
        if acts2 is not None:
            # Filter by date
            if isinstance(acts2, dict) and "activities" in acts2:
                acts = acts2["activities"]
            elif isinstance(acts2, list):
                acts = acts2
            else:
                acts = []
            # Filter to range
            filtered = []
            for a in acts:
                if not isinstance(a, dict):
                    continue
                # Try to parse start date
                s = a.get("startTimeLocal") or a.get("startTimeGMT") or a.get("calendarDate") or ""
                try:
                    # Keep if date string contains our range
                    adate = s[:10] if len(s) >= 10 else ""
                    if adate and start <= adate <= end:
                        filtered.append(a)
                    elif not adate:
                        filtered.append(a)  # keep if unknown
                except Exception:
                    filtered.append(a)
            acts = filtered
        else:
            print(f"  Could not fetch activities: {err2}")
            acts = []

    if acts is None:
        acts = []
    # Normalize acts to list
    if isinstance(acts, dict) and "activities" in acts:
        acts = acts["activities"]
    if not isinstance(acts, list):
        acts = [acts] if acts else []

    for a in acts:
        if not isinstance(a, dict):
            continue
        # Garmin returns activityId and activityName etc.
        aid = a.get("activityId") or a.get("activityIdStr") or a.get("id") or "unknown"
        name = a.get("activityName") or a.get("activityType", {}).get("typeKey") or a.get("activityType") or "Workout"
        if isinstance(name, dict):
            name = name.get("typeKey") or name.get("typeId") or str(name)
        sport = a.get("activityType", {})
        if isinstance(sport, dict):
            sport = sport.get("typeKey") or sport.get("typeId") or "activity"
        else:
            sport = str(sport) if sport else "activity"
        # Date
        adate = "unknown"
        try:
            s = a.get("startTimeLocal") or a.get("startTimeGMT") or ""
            if s and len(s) >= 10:
                adate = s[:10]
            elif a.get("calendarDate"):
                adate = a.get("calendarDate")
        except Exception:
            pass
        # Duration, distance
        duration = a.get("duration") or a.get("elapsedDuration") or a.get("movingDuration")
        distance = a.get("distance") or a.get("totalDistance")
        calories_a = a.get("calories") or a.get("activeKilocalories")
        avg_hr = a.get("averageHR") or a.get("avgHR")
        max_hr = a.get("maxHR")
        # Normalize duration to human
        dur_h = "N/A"
        if duration is not None:
            try:
                dur_h = _format_duration(float(duration))
            except Exception:
                dur_h = str(duration)
        dist_km_a = "N/A"
        if distance is not None:
            try:
                dist_km_a = round(float(distance) / 1000, 2)
            except Exception:
                dist_km_a = distance

        # Promote elevation + keep _raw minimal (Phase 2 bloat fix)
        elev = a.get("elevationGain")
        if elev is None:
            elev = a.get("elevationGainInMeters") or a.get("totalElevationGain")
        min_raw = {}
        if a.get("activityId") is not None:
            min_raw["activityId"] = a.get("activityId")
        if elev is not None:
            min_raw["elevationGain"] = elev
        # Garmin's activity *list* names these avgPower / maxPower / normPower
        # (the detail endpoint's summaryDTO uses averagePower / normalizedPower).
        # Only maxPower ever matched before, so avg/NP were silently dropped.
        # Stored under the names the dashboards already read.
        for out_key, src_keys in (
            ("averagePower", ("avgPower", "averagePower")),
            ("maxPower", ("maxPower",)),
            ("normalizedPower", ("normPower", "normalizedPower")),
            ("max20MinPower", ("max20MinPower",)),
        ):
            for sk in src_keys:
                if _is_num(a.get(sk)):
                    min_raw[out_key] = a.get(sk)
                    break
        act_vo2 = a.get("vO2MaxValue")
        activities.append({
            "id": str(aid),
            "date": adate,
            "name": str(name),
            "sport": str(sport),
            "duration": dur_h,
            "duration_seconds": duration,
            "distance_km": dist_km_a,
            "calories": calories_a if calories_a is not None else "N/A",
            "avg_hr": avg_hr if avg_hr is not None else "N/A",
            "max_hr": max_hr if max_hr is not None else "N/A",
            "elevation_gain": elev if isinstance(elev, (int, float)) else None,
            "vo2max": act_vo2 if _is_num(act_vo2) and act_vo2 > 0 else None,
            "_raw": min_raw,
        })

    # Merge with stored activities: Garmin's list is authoritative for the synced
    # range when the call worked; anything stored outside the range is kept.
    old_acts = (existing or {}).get("activities") or []
    old_ids = {str(a.get("id")) for a in old_acts if isinstance(a, dict)}
    fetched_ok = acts_ok and bool(activities or not old_acts)
    if fetched_ok:
        new_ids = {a["id"] for a in activities}
        keep = [a for a in old_acts if isinstance(a, dict) and (str(a.get("date", "")) < start or str(a.get("date", "")) > end) and str(a.get("id")) not in new_ids]
        activities = keep + activities
    else:
        print("  Note: activity list unavailable — keeping stored activities")
        by_id = {str(a.get("id")): a for a in old_acts if isinstance(a, dict)}
        for a in activities:
            by_id[a["id"]] = a
        activities = list(by_id.values())
    # Notes are only (re)written for rides that are new or recent
    note_ids = [a["id"] for a in activities if a["id"] not in old_ids or str(a.get("date", "")) >= reuse_before]

    # Sort activities by date then id
    try:
        activities.sort(key=lambda x: (x["date"], x["id"]))
    except Exception:
        pass

    # Backfill daily VO2 max from the ride's own estimate when the per-day
    # max-metrics call came back empty (the activity carries the same number).
    for a in activities:
        v = a.get("vo2max")
        d = daily.get(a.get("date"))
        if d is None or not _is_num(v):
            continue
        key = "vo2max_cycling" if "biking" in a["sport"] or "cycling" in a["sport"] else "vo2max_running" if "running" in a["sport"] else None
        if key and d.get(key) is None:
            d[key] = v

    # Full history = everything stored before + what this run fetched
    fetched_dates = sorted(daily)
    merged = dict(old_daily)
    merged.update(daily)
    merged = {k: merged[k] for k in sorted(merged)}

    profile = fetch_profile(client, end)
    # If the "latest" endpoint gave nothing, fall back to the newest value stored.
    for sport in ("cycling", "running"):
        if profile["vo2max"].get(sport) is None:
            for cdate in sorted(merged, reverse=True):
                v = merged[cdate].get(f"vo2max_{sport}")
                if _is_num(v):
                    profile["vo2max"][sport] = {"value": v, "date": cdate}
                    break
    print(f"Profile: VO2max={profile['vo2max']} FTP={profile['ftp']} HRzones={profile['hr_zones']}")

    if deferred:
        print(f"  {len(deferred)} older day(s) left for the next run (oldest {min(deferred)}).")
    print(f"Fetched {len(daily)} day(s), {reused} already stored; history now {len(merged)} day(s) "
          f"({next(iter(merged), '-')} -> {end}) and {len(activities)} activit{'y' if len(activities)==1 else 'ies'}.")
    return {"daily": merged, "activities": activities, "date_range": [next(iter(merged), start), end],
            "profile": profile, "fetched_dates": fetched_dates, "note_activity_ids": note_ids}

def write_markdown_and_json(data, out_dir: Path, dry_run: bool = False):
    """Write garmin/ folder: data.json + daily notes + activity notes."""
    daily = data["daily"]
    activities = data["activities"]
    start, end = data["date_range"]
    # Only rewrite notes for days/rides touched by this run (history notes stay as they are)
    note_days = set(data.get("fetched_dates") or daily.keys())
    note_ids = set(data.get("note_activity_ids") or [a["id"] for a in activities])

    if dry_run:
        print("\n--- DRY RUN PREVIEW (no files written) ---")
        for cdate, d in daily.items():
            if cdate not in note_days:
                continue
            print(f"\n[{cdate}] wellness: steps={d['steps']} sleep={d['sleep_hours']} ({d['sleep_score']}) hrv={d['hrv']} rhr={d['resting_hr']} bb={d['body_battery']} stress={d['stress']} readiness={d['training_readiness']}")
        if activities:
            print(f"\nActivities ({len(activities)}):")
            for a in activities:
                print(f"  {a['date']} — {a['sport']} — {a['name']} — {a['duration']} — {a['distance_km']} km — {a['calories']} kcal — HR {a['avg_hr']}/{a['max_hr']}")
        else:
            print("\nNo activities in this window (that's normal if you rested).")
        print("\nDry run done — no files were saved.")
        return

    # Create folders
    out_dir.mkdir(parents=True, exist_ok=True)
    daily_dir = out_dir / "daily"
    act_dir = out_dir / "activities"
    daily_dir.mkdir(exist_ok=True)
    act_dir.mkdir(exist_ok=True)

    # Write data.json (strip _raw if you want smaller file? Keep it for richness)
    # We keep daily without _raw in markdown but include full in JSON for AI reading
    json_path = out_dir / "data.json"
    # Prepare JSON without circular issues — ensure serializable
    serializable = {
        "generated_at": _now_ist().isoformat(),
        "date_range": [start, end],
        "profile": data.get("profile") or {},
        "daily": daily,
        "activities": activities,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=str)
    print(f"Wrote {json_path} ({len(daily)} days, {len(activities)} activities)")

    # Write one wellness markdown per day — plain English
    for cdate, d in daily.items():
        if cdate not in note_days:
            continue
        md = f"""# {cdate} — Wellness

> Recovery data Strava can't give you. Read-only from Garmin Connect.

- **Steps:** {d['steps']}
- **Distance:** {d['distance_km']} km
- **Calories:** {d['calories']} kcal
- **Sleep:** {d['sleep_hours']} (score: {d['sleep_score']})
  - Deep: {d['sleep_deep']} | Light: {d['sleep_light']} | REM: {d['sleep_rem']} | Awake: {d['sleep_awake']}
- **HRV (last night avg):** {d['hrv']}
- **Resting HR:** {d['resting_hr']} bpm
- **Body Battery (highest):** {d['body_battery']}
- **Stress (avg):** {d['stress']}
- **Training Readiness:** {d['training_readiness']}
- **VO2 max update:** {d.get('vo2max_cycling') or d.get('vo2max_running') or '— (only updates after a qualifying workout)'}

*Generated {_now_ist().strftime('%Y-%m-%d %H:%M %Z')} — data.json has full detail.*

---

## What this means (plain English)

- Sleep hours + score tell you if you recovered overnight.
- HRV and resting HR together show if your body is stressed or fresh.
- Body Battery is Garmin's energy estimate (higher = more ready to train).
- Stress is Garmin's 0-100 estimate (lower is calmer).
- Training Readiness combines them — use it to decide easy vs hard days.
"""
        p = daily_dir / f"{cdate}.md"
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  Wrote {p}")

    # Write one note per workout
    for a in activities:
        if a["id"] not in note_ids:
            continue
        # Sanitize filename
        safe_sport = "".join(c if c.isalnum() or c in "-_" else "-" for c in a['sport'])[:20]
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in a['name']).strip().replace(" ", "-")[:30]
        fname = f"{a['date']}-{a['id']}-{safe_sport}.md"
        # If date unknown, avoid duplicate
        if a['date'] == "unknown":
            fname = f"activity-{a['id']}-{safe_sport}.md"
        p = act_dir / fname
        # Group raw details for markdown
        md = f"""# {a['date']} — {a['name']} ({a['sport']})

- **Activity ID:** {a['id']}
- **Sport:** {a['sport']}
- **Duration:** {a['duration']}
- **Distance:** {a['distance_km']} km
- **Calories:** {a['calories']} kcal
- **Avg HR:** {a['avg_hr']} bpm | **Max HR:** {a['max_hr']} bpm

*Generated {_now_ist().strftime('%Y-%m-%d %H:%M %Z')} — full raw data is in data.json.*

---

### Notes

- One note per workout keeps training history easy to browse.
- Pair this with the wellness note for the same date to see recovery impact.
"""
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  Wrote {p}")

    # Also write friendly index
    index_md = out_dir / "README.md"
    with open(index_md, "w", encoding="utf-8") as f:
        f.write(f"""# Garmin data — last sync {_now_ist().strftime('%Y-%m-%d %H:%M %Z')}

This folder is read-only from Garmin. It updates when you run `py sync_garmin.py`.

- `data.json` — structured data for AI to read (daily wellness + activities)
- `daily/YYYY-MM-DD.md` — one wellness note per day (sleep, HRV, RHR, body battery, stress, training readiness, steps)
- `activities/YYYY-MM-DD-<id>-<sport>.md` — one note per workout

## Quick stats: {start} to {end}

- Days: {len(daily)}
- Activities: {len(activities)}

## How to use

- Ask your AI: "read my garmin folder and tell me how recovered I am"
- Re-sync any time: `py sync_garmin.py --days 7 --sink files --out ./garmin`
""")
    print(f"Wrote {index_md}")
    print(f"\nDone! Your Garmin folder is at {out_dir.resolve()}")

def main():
    parser = argparse.ArgumentParser(
        description="Garmin -> markdown sync (read-only). Pulls workouts + wellness (sleep, HRV, RHR, body battery, stress, training readiness) into garmin/ folder.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="Examples:\n"
               "  py sync_garmin.py --login\n"
               "  py sync_garmin.py --days 3 --dry-run\n"
               "  py sync_garmin.py --days 3 --sink files --out ./garmin\n"
               "  py sync_garmin.py --days 7 --sink files --out ./garmin\n"
    )
    parser.add_argument("--login", action="store_true", help="One-time Garmin login (saves private token, never stores password)")
    parser.add_argument("--days", type=int, default=7, help="Number of recent days to pull (default 7)")
    parser.add_argument("--months", type=int, default=None,
                        help="Calendar-month window instead of --days: current month + the previous N-1 whole months "
                             "(e.g. --months 2 on Nov 9 = Oct 1 -> Nov 9). Reuses older days from the existing data.json.")
    parser.add_argument("--since", default=None,
                        help="Keep history back to this date (YYYY-MM-DD): any missing day since then is backfilled "
                             f"(max {MAX_BACKFILL_DAYS} per run, newest first). Stored days are never dropped.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print preview without writing files")
    parser.add_argument("--sink", choices=["files"], default="files", help="Where to save (only 'files' supported)")
    parser.add_argument("--out", default="./garmin", help="Output folder (default ./garmin)")
    parser.add_argument("--tokenstore", default=TOKENSTORE_DEFAULT, help="Token folder (default ~/.garminconnect) — keep private")

    args = parser.parse_args()

    tokenstore = args.tokenstore
    # Ensure path uses expanded user
    tokenstore_expanded = str(Path(tokenstore).expanduser())

    if args.login:
        ok = do_login(tokenstore_expanded)
        sys.exit(0 if ok else 1)

    # Normal sync: need to be logged in
    try:
        from garminconnect import Garmin
    except ImportError:
        print("Missing garminconnect. Install with: py -m pip install -r requirements.txt")
        sys.exit(1)

    # Validate days
    if args.months is not None and not (1 <= args.months <= 12):
        print("--months must be 1..12")
        sys.exit(1)
    if args.days < 1 or args.days > 365:
        print("--days must be 1..365")
        sys.exit(1)
    since_date = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since.strip())
        except ValueError:
            print("--since must be a date like 2026-07-01")
            sys.exit(1)
        if since_date > _today_ist():
            print("--since can't be in the future")
            sys.exit(1)

    # Try to load existing tokens, with credential fallback for CI self-healing.
    # Newer garminconnect (>=0.3.6) auto-discards poisoned/expired cached tokens
    # and does a fresh credential login — but ONLY if email+password were passed
    # to the Garmin() constructor. So always pass them when available via env.
    # This fixes the daily "No valid saved token (Failed to retrieve social profile)"
    # failure: previously the Action only had the token file, so an expired token
    # was fatal. Now GARMIN_EMAIL/GARMIN_PASSWORD secrets let it re-login alone.
    email_env = os.getenv("GARMIN_EMAIL")
    password_env = os.getenv("GARMIN_PASSWORD")
    client = None
    logged_in = False
    try:
        # First try token-only
        if email_env and password_env:
            client = Garmin(email=email_env, password=password_env)
        else:
            client = Garmin()
        client.login(tokenstore_expanded)
        print(f"Logged in via saved token ({tokenstore_expanded}) — no password needed.")
        logged_in = True
    except Exception as e:
        print(f"No valid saved token ({e}).")
        # Non-interactive (GitHub Actions): try credential re-login once, else fail actionable
        if email_env and password_env:
            print("Saved token rejected/expired — trying fresh credential login (self-healing)...")
            try:
                # Fresh client so poisoned cache can't short-circuit the strategy chain
                client = Garmin(email=email_env, password=password_env)
                client.login(tokenstore_expanded)
                print("Credential re-login succeeded — refreshed token saved for this run.")
                print("NOTE: runners are ephemeral, so update the GARMINTOKENS repo secret")
                print("with the refreshed file when convenient (or leave email/password secrets — they self-heal).")
                logged_in = True
            except Exception as e2:
                print(f"Credential re-login also failed: {e2}")
                print("Action needed:")
                print("  1. Run locally: py sync_garmin.py --login, then update GARMINTOKENS secret, OR")
                print("  2. Set GARMIN_EMAIL + GARMIN_PASSWORD repo secrets (recommended, self-healing).")
                print("  3. If rate-limited (429) or MFA required, wait a few minutes and re-run.")
                sys.exit(1)
        else:
            print("Run: py sync_garmin.py --login  (you'll type email + hidden password once)")
            print("CI fix: add GARMIN_EMAIL + GARMIN_PASSWORD as repo secrets so the workflow can self-heal,")
            print("or refresh the GARMINTOKENS secret with a fresh local login.")
        # As a convenience, offer inline login if interactive (only if still not logged in)
        if not logged_in and sys.stdin.isatty():
            print("\nYou can log in now instead. Press Enter to continue or Ctrl+C to cancel.")
            try:
                input()
            except KeyboardInterrupt:
                sys.exit(1)
            if do_login(tokenstore_expanded):
                try:
                    client = Garmin()
                    client.login(tokenstore_expanded)
                except Exception as e2:
                    print(f"Still can't log in: {e2}")
                    sys.exit(1)
            else:
                sys.exit(1)
        elif not logged_in:
            sys.exit(1)

    # Ensure permissions on token file
    _ensure_tokenstore_dir(tokenstore_expanded)

    # Fetch
    try:
        # data.json is a growing history: load it so nothing stored is dropped
        existing = None
        try:
            existing = json.loads((Path(args.out) / "data.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        today = _today_ist()
        starts = []
        if since_date:
            starts.append(since_date)
        if args.months:
            starts.append(month_window_start(today, args.months))
        if not starts:
            starts.append(today - timedelta(days=args.days - 1))
        data = fetch_days(client, start_date=min(starts), existing=existing)
    except Exception as e:
        print(f"Failed to fetch data: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Write or preview
    out_dir = Path(args.out)
    # If out is relative, resolve relative to script location or cwd? Use cwd as user expects
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    write_markdown_and_json(data, out_dir, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
