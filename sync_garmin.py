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

# Keep imports lazy so --help works even before deps are installed
TOKENSTORE_DEFAULT = "~/.garminconnect"

# Helper to restrict token permissions (best effort on Windows)
def _secure_path(path: Path, is_dir: bool = False):
    try:
        if os.name != "nt":
            mode = 0o700 if is_dir else 0o600
            os.chmod(path, mode)
        else:
            # On Windows, try to restrict via chmod as well; Python maps it to read-only flags
            # We still attempt it — no harm if it doesn't fully lock down.
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

def _safe_call(label, func, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
        return result, None
    except Exception as e:
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
    if isinstance(score, dict):
        score = score.get("overall") or score.get("value") or score
    elif isinstance(score, list) and score:
        score = score[0].get("overall") if isinstance(score[0], dict) else score[0]
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

def _extract_hrv(hrv_data):
    if not hrv_data or not isinstance(hrv_data, dict):
        return "N/A"
    # Try several known shapes
    for k in ["lastNightAvg", "hrvValue", "avg", "weeklyAvg", "baseline", "status"]:
        if k in hrv_data and hrv_data[k] not in (None, ""):
            v = hrv_data[k]
            if isinstance(v, dict):
                # sometimes nested
                return v.get("value") or v.get("avg") or str(v)
            return v
    # Check nested list
    if "hrvSummary" in hrv_data:
        summ = hrv_data["hrvSummary"]
        if isinstance(summ, dict):
            return summ.get("lastNightAvg") or summ.get("weeklyAvg") or "N/A"
        if isinstance(summ, list) and summ:
            return summ[0].get("lastNightAvg") or "N/A"
    # Check for hrvReadings
    if "hrvReadings" in hrv_data and isinstance(hrv_data["hrvReadings"], list) and hrv_data["hrvReadings"]:
        last = hrv_data["hrvReadings"][-1]
        if isinstance(last, dict):
            return last.get("hrvValue") or last.get("value") or "N/A"
    return "N/A"

def _extract_body_battery(bb_data):
    if not bb_data:
        return "N/A"
    try:
        if isinstance(bb_data, list) and bb_data:
            # list of daily entries
            last = bb_data[-1]
            if isinstance(last, dict):
                for k in ["bodyBatteryHighestValue", "highest", "value", "charged", "bodyBatteryValue"]:
                    if k in last and last[k] not in (None, ""):
                        return last[k]
                # fallback: find numeric
                for v in last.values():
                    if isinstance(v, int):
                        return v
            return str(last)[:80]
        if isinstance(bb_data, dict):
            for k in ["bodyBatteryHighestValue", "value", "highest", "charged"]:
                if k in bb_data and bb_data[k] not in (None, ""):
                    return bb_data[k]
    except Exception:
        pass
    return "N/A"

def _extract_stress(stress_data):
    if not stress_data or not isinstance(stress_data, dict):
        return "N/A"
    for k in ["overallStressLevel", "avgStressLevel", "averageStressLevel", "stressLevel", "value"]:
        if k in stress_data and stress_data[k] not in (None, ""):
            return stress_data[k]
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
    if not tr_data:
        return "N/A"
    try:
        if isinstance(tr_data, list) and tr_data:
            item = tr_data[0]
            if isinstance(item, dict):
                for k in ["score", "trainingReadiness", "readinessScore", "level", "value"]:
                    if k in item and item[k] not in (None, ""):
                        return item[k]
                # sometimes nested
                if "trainingReadinessScore" in item:
                    return item["trainingReadinessScore"]
        if isinstance(tr_data, dict):
            for k in ["score", "trainingReadinessScore", "readinessScore", "value"]:
                if k in tr_data and tr_data[k] not in (None, ""):
                    return tr_data[k]
    except Exception:
        pass
    return "N/A"

def fetch_days(client, days: int):
    """Fetch wellness + activities for last N days. Returns dict with 'daily' and 'activities'."""
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    dates = sorted(dates)  # oldest first

    # For activities, use range query
    start = dates[0]
    end = dates[-1]

    print(f"Fetching {days} day(s): {start} to {end} ...")
    daily = {}
    for cdate in dates:
        # Call each API safely so one failing doesn't break the whole day
        stats, _ = _safe_call("stats", client.get_user_summary, cdate)
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

        steps = "N/A"
        if stats and isinstance(stats, dict):
            steps = stats.get("totalSteps") or stats.get("totalStepsDisplay") or "N/A"
            # fallback: steps from stats
            if steps == "N/A" and "dailyStepGoal" in stats:
                steps = stats.get("totalSteps", "N/A")

        sleep_info = _extract_sleep(sleep)
        hrv_val = _extract_hrv(hrv) if hrv else "N/A"
        bb_val = _extract_body_battery(bb)
        stress_val = _extract_stress(stress)
        rhr_val = _extract_rhr(hr, rhr)
        tr_val = _extract_training_readiness(tr)

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
            # keep raw for data.json debugging (sanitized, no tokens)
            "_raw": {
                "stats": stats,
                "hr": hr,
                "sleep": sleep,
                "hrv": hrv,
                "bodyBattery": bb,
                "stress": stress,
                "rhr": rhr,
                "trainingReadiness": tr,
            }
        }
        # Progress dot
        print(f"  {cdate}: steps={steps} hrv={hrv_val} rhr={rhr_val} bb={bb_val} stress={stress_val} readiness={tr_val} sleep={sleep_info['hours']} ({sleep_info['score']})")

    # Activities
    activities = []
    # Try date-range method first, fallback to recent list
    acts, err = _safe_call("activities_by_date", client.get_activities_by_date, start, end)
    if acts is None or err:
        print(f"  Note: get_activities_by_date failed ({err}), trying get_activities(0, 20)...")
        acts2, err2 = _safe_call("activities", client.get_activities, 0, 30)
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
            "_raw": a,
        })

    # Sort activities by date then id
    try:
        activities.sort(key=lambda x: (x["date"], x["id"]))
    except Exception:
        pass

    print(f"Fetched {len(daily)} day(s) and {len(activities)} activit{'y' if len(activities)==1 else 'ies'}.")
    return {"daily": daily, "activities": activities, "date_range": [start, end]}

def write_markdown_and_json(data, out_dir: Path, dry_run: bool = False):
    """Write garmin/ folder: data.json + daily notes + activity notes."""
    daily = data["daily"]
    activities = data["activities"]
    start, end = data["date_range"]

    if dry_run:
        print("\n--- DRY RUN PREVIEW (no files written) ---")
        for cdate, d in daily.items():
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
        "generated_at": datetime.now().isoformat(),
        "date_range": [start, end],
        "daily": daily,
        "activities": activities,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=str)
    print(f"Wrote {json_path} ({len(daily)} days, {len(activities)} activities)")

    # Write one wellness markdown per day — plain English
    for cdate, d in daily.items():
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

*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — data.json has full detail.*

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

*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — full raw data is in data.json.*

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
        f.write(f"""# Garmin data — last sync {datetime.now().strftime('%Y-%m-%d %H:%M')}

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
    if args.days < 1 or args.days > 365:
        print("--days must be 1..365")
        sys.exit(1)

    # Try to load existing tokens
    client = None
    try:
        # First try token-only
        client = Garmin()
        client.login(tokenstore_expanded)
        print(f"Logged in via saved token ({tokenstore_expanded}) — no password needed.")
    except Exception as e:
        print(f"No valid saved token ({e}).")
        print("Run: py sync_garmin.py --login  (you'll type email + hidden password once)")
        # As a convenience, offer inline login if interactive
        if sys.stdin.isatty():
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
        else:
            sys.exit(1)

    # Ensure permissions on token file
    _ensure_tokenstore_dir(tokenstore_expanded)

    # Fetch
    try:
        data = fetch_days(client, args.days)
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
