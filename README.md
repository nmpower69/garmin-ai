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

## Automated daily sync

The daily 10:00 AM IST sync is triggered by **AWS EventBridge Scheduler**, which calls
the GitHub `workflow_dispatch` API. This avoids GitHub's own cron scheduler, which is
best-effort and has been observed firing 6+ hours late.

**Trigger paths (in order of reliability):**
1. **EventBridge Scheduler** — primary, fires on time every day at 10:00 AM IST
2. **GitHub cron** (`30 4 * * *` = 10:00 AM IST) — fallback only, in the workflow file

If both land on the same morning the sync simply runs twice; the second run finds no
data changes and skips the commit. The `concurrency` group prevents overlap.

### Why the timing matters
You ride at 6:20 AM and read the briefing on your phone. A 6-hour delay in the sync
means the dashboard and briefing show stale data until the afternoon.

### Troubleshooting
- Check trigger: **AWS Console → EventBridge → Scheduler → `garmin-daily-sync` → Recent invocations**
- Check the run itself: **GitHub → Actions → Garmin Daily Sync**
- Test on demand: **GitHub → Actions → Garmin Daily Sync → Run workflow**
- Sync locally: `py sync_garmin.py --days 3 --dry-run`

Security: password is never saved or shown. Token is saved with private permissions and auto-refreshes. Script is read-only — it never writes to Garmin.
