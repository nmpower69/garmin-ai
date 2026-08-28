# garmin-ai — your Garmin data for AI (read-only)

This folder syncs your Garmin workouts + recovery data (sleep, HRV, resting HR, body battery, stress, training readiness) into plain-English markdown + JSON that your AI can read.

- `sync_garmin.py` — the sync script (security-hardened, read-only)
- `requirements.txt` — Python deps (garminconnect + curl_cffi)
- `garmin/` — your data lives here after sync:
  - `garmin/data.json` — structured data
  - `garmin/daily/YYYY-MM-DD.md` — one wellness note per day
  - `garmin/activities/YYYY-MM-DD-<id>-<sport>.md` — one note per workout

## First-time setup (done automatically)
- `py --version` → Python 3.12.10 installed via winget
- `py -m pip install -r requirements.txt` → done

## How to sync
1. **Login once:** `py sync_garmin.py --login`  (hidden password prompt, saves private token to `~/.garminconnect`)
2. **Test:** `py sync_garmin.py --days 3 --dry-run`
3. **Save files:** `py sync_garmin.py --days 3 --sink files --out ./garmin`

## Auto-sync (optional)
After testing, ask to set up Windows Task Scheduler to run every morning at 7am:
`py sync_garmin.py --days 7 --sink files --out ./garmin`

Security: password is never saved or shown. Token is saved with private permissions and auto-refreshes. Script is read-only — it never writes to Garmin.
