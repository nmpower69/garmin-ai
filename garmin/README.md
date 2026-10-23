# Garmin data — last sync 2026-10-23 14:57 IST

This folder is read-only from Garmin. It updates when you run `py sync_garmin.py`.

- `data.json` — structured data for AI to read (daily wellness + activities)
- `daily/YYYY-MM-DD.md` — one wellness note per day (sleep, HRV, RHR, body battery, stress, training readiness, steps)
- `activities/YYYY-MM-DD-<id>-<sport>.md` — one note per workout

## Quick stats: 2026-09-24 to 2026-10-23

- Days: 30
- Activities: 15

## How to use

- Ask your AI: "read my garmin folder and tell me how recovered I am"
- Re-sync any time: `py sync_garmin.py --days 7 --sink files --out ./garmin`
