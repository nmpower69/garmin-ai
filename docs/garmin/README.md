# Garmin data — last sync 2026-08-30 10:01 IST

This folder is read-only from Garmin. It updates when you run `py sync_garmin.py`.

- `data.json` — structured data for AI to read (daily wellness + activities)
- `daily/YYYY-MM-DD.md` — one wellness note per day (sleep, HRV, RHR, body battery, stress, training readiness, steps)
- `activities/YYYY-MM-DD-<id>-<sport>.md` — one note per workout

## Quick stats: 2026-08-01 to 2026-08-30

- Days: 30
- Activities: 11

## How to use

- Ask your AI: "read my garmin folder and tell me how recovered I am"
- Re-sync any time: `py sync_garmin.py --days 7 --sink files --out ./garmin`
