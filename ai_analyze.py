#!/usr/bin/env python3
"""
AI daily analysis for cycling — uses OpenRouter model nvidia/nemotron-3-ultra-550b-a55b:free

Reads garmin/data.json + garmin/power_curves.json (fresh from sync)
Calls OpenRouter chat/completions and writes:
  - garmin/ai_insights.md  (human markdown, committed)
  - garmin/ai_insights.json (structured for dashboard)

Env required: OPENROUTER_API_KEY
Model: nvidia/nemotron-3-ultra-550b-a55b:free  (free tier via OpenRouter)

Run locally:  OPENROUTER_API_KEY=sk-or-v1-... python ai_analyze.py
In GitHub Actions: secret OPENROUTER_API_KEY is injected.
"""
import json, os, sys, pathlib, datetime, textwrap, requests

MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DATA_JSON = pathlib.Path("garmin/data.json")
CURVES_JSON = pathlib.Path("garmin/power_curves.json")
OUT_MD = pathlib.Path("garmin/ai_insights.md")
OUT_JSON = pathlib.Path("garmin/ai_insights.json")

def load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: could not load {p}: {e}")
        return None

def build_prompt(data, curves):
    # Compact summary for LLM — keep under ~8k tokens
    daily = data.get("daily", {}) if data else {}
    acts = [a for a in data.get("activities", []) if a.get("sport")=="road_biking"] if data else []
    # Last 7 days daily
    sorted_daily = sorted(daily.items())[-7:]
    daily_lines = []
    for d, v in sorted_daily:
        daily_lines.append(f"{d}: steps={v.get('steps')} sleep={v.get('sleep_hours')} ({v.get('sleep_score')}) hrv={v.get('hrv')} rhr={v.get('resting_hr')} bb={v.get('body_battery')} stress={v.get('stress')} readiness={v.get('training_readiness')}")
    # Last 7 rides compact
    ride_lines=[]
    for a in sorted(acts, key=lambda x: x["date"])[-7:]:
        ride_lines.append(f"{a['date']} {a['name']} {a['distance_km']}km {a['duration']} avgHR={a.get('avg_hr')} maxHR={a.get('max_hr')} elev={a.get('_raw',{}).get('elevationGain')} cals={a.get('calories')}")
    # Power curves compact
    curves_lines=[]
    if curves and "curves" in curves:
        for k,v in list(curves["curves"].items())[-7:]:
            if isinstance(v, dict) and "10s" in v:
                curves_lines.append(f"{v.get('date')} {k[-5:]}: 10s={v.get('10s')} 30s={v.get('30s')} 60s={v.get('60s')} 5m={v.get('300s')} 20m={v.get('1200s')} 1h={v.get('3600s')} avg={v.get('avgPower')} NP={v.get('normalizedPower')} max={v.get('maxPower')}")
    # FTP/HR zones from previous analysis (hardcoded from garmin profile, but also include if in data)
    zones = "HR zones: LTHR 175, Max 196 => Z1<139, Z2 139-159, Z3 159-167, Z4 167-172, Z5>172. Power zones: FTP 271W (stale 2025-03-04) => Z1<148, Z2 148-203, Z3 203-244, Z4 244-284, Z5 284-324, Z6 324-406, Z7>406. VO2max null."

    prompt = f"""You are a cycling coach for a casual happiness rider, not a racer. The rider is a cyclist (outdoor road_biking), restarted after a break, never rides >3h, wants joyful improvement.

Context: Today is {datetime.date.today().isoformat()}. Data window last 7-30 days. The rider is in Kolhapur, India, flat-rolling terrain.

Daily wellness last 7 days:
{chr(10).join(daily_lines)}

Recent rides last 7:
{chr(10).join(ride_lines)}

Best power per ride (real directPower rolling maxima):
{chr(10).join(curves_lines)}

Additional: {zones}
FTP is stale (271W from 2025-03-04). 20m bests are 143-204W, so true FTP likely ~150-195W. Training readiness, HRV, RHR, sleep are volatile.

Task: Generate exactly 10 insights as JSON array. Each insight must have: "title" (short, like "Insight 1: Aug 12 ride breached easy ceiling by ~8 bpm") and "description" (2-3 sentences, plain English, specific to cycling, referencing actual dates/numbers above, actionable for happiness). Make them parallel to these running examples but cycling-appropriate:
Insight 1: Aug 20 run, breached easy ceiling by 3 beats.
Insight 2: Long run, vertical load is high for a road marathon block
Insight 3: Three-run day August 24 flags recovery sequencing risk
Insight 4: Marathon progression (now Cycling progression)
Insight 5: Zone model (recalibrated 2026-08-09)
Insight 6: Polarized 80/20
Insight 7: Recovery/warning signs
Insight 8: Strength - maximal/reactive
Insight 9: Activity suggestion for Vo2max improvements.
Insight 10: FTP suggestions

For cycling, adapt: e.g., Insight 1 = easy ride too hard, Insight 2 = long ride vert load, Insight 3 = high ride density week, etc. Use actual numbers, not generic. Be concise, friendly, no racing pressure. Return ONLY JSON array, no markdown, no extra text. Example format:
[
  {{"title": "Insight 1: ...", "description": "...", "badge": "warn", "badgeText": "Zone drift"}},
  ...
]
Badge must be one of ok/warn/bad.

If any data missing (e.g., no power), say so in description but still produce 10.
"""
    return prompt

def call_openrouter(prompt, api_key):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Optional but recommended by OpenRouter
        "HTTP-Referer": "https://github.com/nmpower69/garmin-ai",
        "X-Title": "garmin-ai cycling dashboard",
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful cycling coach. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 3000,
    }
    print(f"Calling OpenRouter {MODEL}...")
    resp = requests.post(url, headers=headers, json=body, timeout=90)
    print(f"OpenRouter status {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:2000])
        resp.raise_for_status()
    j = resp.json()
    # OpenRouter returns choices[0].message.content
    content = j["choices"][0]["message"]["content"] if j.get("choices") else ""
    # Sometimes wrapped in ```json ... ```
    if "```" in content:
        # extract between ```json and ```
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if m:
            content = m.group(1)
    return content.strip()

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("::warning:: OPENROUTER_API_KEY not set — writing placeholder and exiting 0 (so workflow doesn't fail)")
        placeholder = [
            {"title": f"Insight {i+1}: AI not configured yet", "description": "Add OPENROUTER_API_KEY as GitHub secret and re-run workflow to generate real cycling insights from your Garmin data.", "badge": "warn", "badgeText": "Setup"}
            for i in range(10)
        ]
        OUT_JSON.write_text(json.dumps(placeholder, indent=2), encoding="utf-8")
        OUT_MD.write_text("# AI Insights — not configured yet\n\nAdd `OPENROUTER_API_KEY` as a repo secret (Settings → Secrets and variables → Actions) and re-run the Garmin Daily Sync workflow.\n\nOnce set, this file will show 10 fresh cycling insights each morning at 10 AM IST.\n", encoding="utf-8")
        print(f"Wrote placeholder {OUT_JSON} and {OUT_MD}")
        sys.exit(0)

    data = load_json(DATA_JSON)
    curves = load_json(CURVES_JSON)
    if not data:
        print("ERROR: garmin/data.json missing — run sync first")
        sys.exit(1)

    prompt = build_prompt(data, curves)
    print("Prompt chars:", len(prompt))
    # For debugging, also write prompt locally (not committed) — optional
    pathlib.Path("garmin/ai_prompt.txt").write_text(prompt, encoding="utf-8")

    try:
        content = call_openrouter(prompt, api_key)
        print("Raw LLM output head:", content[:500])
        # Validate JSON
        insights = json.loads(content)
        if not isinstance(insights, list) or len(insights) != 10:
            raise ValueError(f"Expected 10 insights, got {len(insights) if isinstance(insights, list) else type(insights)}")
        # Basic validation of fields
        for idx, ins in enumerate(insights):
            if "title" not in ins or "description" not in ins:
                raise ValueError(f"Insight {idx} missing title/description: {ins}")
            ins.setdefault("badge", "ok")
            ins.setdefault("badgeText", "AI")
        # Write JSON for dashboard
        OUT_JSON.write_text(json.dumps(insights, indent=2, ensure_ascii=False), encoding="utf-8")
        # Write markdown for humans
        md_lines = [f"# AI Cycling Insights — {datetime.date.today().isoformat()}  (via {MODEL})", ""]
        md_lines.append(f"_Model: `{MODEL}` via OpenRouter — auto-generated after daily Garmin sync._\n")
        for ins in insights:
            md_lines.append(f"### {ins['title']}")
            md_lines.append(f"{ins['description']}\n")
        OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"Wrote {OUT_JSON} and {OUT_MD} with 10 insights")
    except Exception as e:
        print(f"AI call failed: {e}")
        import traceback; traceback.print_exc()
        # Write fallback so workflow still commits something useful, but fail the job to alert
        fallback = [
            {"title": f"Insight {i+1}: AI generation failed — using fallback", "description": f"Error: {e}. Check OPENROUTER_API_KEY and model quota. Your Garmin data is still fresh in garmin/data.json.", "badge": "bad", "badgeText": "Error"}
            for i in range(10)
        ]
        # Don't overwrite if we already have valid previous insights? But write fallback for visibility
        OUT_JSON.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
        OUT_MD.write_text(f"# AI Insights — generation failed {datetime.datetime.now().isoformat()}\n\nError: {e}\n\nCheck `OPENROUTER_API_KEY` secret and OpenRouter quota for model `{MODEL}`.\n", encoding="utf-8")
        # Exit 1 to make workflow show red so you notice, but you can change to 0 to keep green
        sys.exit(1)

if __name__ == "__main__":
    main()
