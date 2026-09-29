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

MODEL = os.getenv("AI_MODEL", "liquid/lfm-2.5-2.6b:free")
# Free-tier models only — no paid key, no BYOK models (nvidia/ultra needs BYOK).
# Order matters: cheap/fast first, then progressively different providers so a
# rate-limited or empty-returning model still leaves healthy candidates behind.
# Each entry has returned valid JSON on the free pool at least once.
FALLBACK_MODELS = [
    "z-ai/glm-5.2:free",
    "minimax/minimax-m3:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "deepseek/deepseek-chat-v3-0324:free",
]
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
        elev = a.get('elevation_gain') if isinstance(a.get('elevation_gain'), (int, float)) else a.get('_raw',{}).get('elevationGain')
        ride_lines.append(f"{a['date']} {a['name']} {a['distance_km']}km {a['duration']} avgHR={a.get('avg_hr')} maxHR={a.get('max_hr')} elev={elev} cals={a.get('calories')}")
    # Power curves compact - sorted by date, not insertion order
    curves_lines=[]
    if curves and "curves" in curves:
        sorted_curves = sorted([kv for kv in curves["curves"].items() if isinstance(kv[1], dict) and "10s" in kv[1]], key=lambda kv: kv[1].get("date",""))
        for k,v in sorted_curves[-7:]:
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

def _extract_content(j):
    """Pull assistant text out of a chat-completions response.

    Free reasoning models are inconsistent: text may land in `content`,
    in `reasoning`, or come back null entirely. Returns a str (possibly "").
    """
    try:
        choices = j.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        parts = [msg.get("content"), msg.get("reasoning"), msg.get("reasoning_content")]
        for p in parts:
            if isinstance(p, str) and p.strip():
                return p
        # Some providers return content as a list of blocks
        c = msg.get("content")
        if isinstance(c, list):
            texts = [b.get("text", "") for b in c if isinstance(b, dict)]
            joined = "".join(t for t in texts if isinstance(t, str))
            if joined.strip():
                return joined
        return ""
    except Exception:
        return ""


def _strip_fences(content):
    """Remove ```json fences, tolerating a missing closing fence."""
    import re
    if "```" not in content:
        return content
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    if m:
        return m.group(1)
    content = re.sub(r"^.*?\n", "", content, count=1) if content.lstrip().startswith("```") else content
    return content.replace("```", "").strip()


def _parse_insights(content):
    """Parse + validate the model's JSON, repairing common free-model truncation.

    Raises ValueError if the payload can't be turned into exactly 10 insights.
    """
    try:
        insights = json.loads(content)
    except json.JSONDecodeError as je:
        print(f"JSON parse failed: {je} — trying to repair truncated output")
        repaired = content
        if "Unterminated string" in str(je):
            repaired = content.rstrip() + '"'
        open_brackets = repaired.count('[') - repaired.count(']')
        open_braces = repaired.count('{') - repaired.count('}')
        repaired += '"}' * max(0, open_braces - open_brackets) if open_braces > open_brackets else ''
        repaired += ']' * max(0, open_brackets) if open_brackets > 0 else ''
        try:
            insights = json.loads(repaired)
        except Exception:
            last_complete = repaired.rfind('},')
            if last_complete == -1:
                raise
            insights = json.loads(repaired[:last_complete + 1] + ']')
            print(f"Repaired truncated JSON to {len(insights)} insights")
    if not isinstance(insights, list) or len(insights) != 10:
        raise ValueError(f"Expected 10 insights, got {len(insights) if isinstance(insights, list) else type(insights)}")
    for idx, ins in enumerate(insights):
        if not isinstance(ins, dict) or "title" not in ins or "description" not in ins:
            raise ValueError(f"Insight {idx} missing title/description")
        ins.setdefault("badge", "ok")
        ins.setdefault("badgeText", "AI")
    return insights


def call_openrouter(prompt, api_key, model=None):
    """Try each free model in turn; return (insights, model_name).

    A model is skipped — never fatal — when it errors, returns non-200, returns
    empty content, or returns content that won't parse into 10 valid insights.
    """
    global MODEL
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/nmpower69/garmin-ai",
        "X-Title": "garmin-ai cycling dashboard",
    }
    primary = model or MODEL
    tried = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    last_err = None
    for mdl in tried:
        # Reasoning models (Nemotron) require reasoning.enabled per OpenRouter sample
        is_reasoning = "nemotron" in mdl or "reasoning" in mdl
        body = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": "You are a helpful cycling coach. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 5000,
        }
        if is_reasoning:
            body["reasoning"] = {"enabled": True}
        print(f"Calling OpenRouter {mdl}...")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=90)
        except Exception as e:
            # Network blip / timeout — treat as a model failure and move on
            print(f"Request error for {mdl}: {e}")
            last_err = f"{mdl}: request error {e}"
            print("→ trying fallback model...")
            continue
        print(f"OpenRouter status {resp.status_code} for {mdl}")
        if resp.status_code == 200:
            content = _strip_fences(_extract_content(resp.json()).strip())
            if not content:
                # 200 but empty/null content — e.g. reasoning model emitted nothing.
                # Must NOT abort the chain; fall through to the next free model.
                print(f"{mdl} returned 200 with empty content → trying fallback model...")
                last_err = f"{mdl}: 200 but empty content"
                continue
            print(f"Got content from {mdl} (raw head {content[:200]!r})")
            # Validate per-model: a model that returns malformed JSON is skipped
            # so the remaining free models still get a chance.
            try:
                insights = _parse_insights(content)
            except Exception as pe:
                print(f"{mdl} returned unusable JSON ({pe}) → trying fallback model...")
                last_err = f"{mdl}: unusable JSON ({pe})"
                continue
            MODEL = mdl
            return insights, mdl
        txt = resp.text[:2000]
        print(txt)
        last_err = f"{mdl}: {resp.status_code} {txt[:500]}"
        # Try next model for any non-200 (401 auth, 404 no endpoint, 429 rate-limit, 5xx)
        # Don't raise immediately — fall back to next free model
        print(f"→ trying fallback model...")
        continue
    raise RuntimeError(f"All models failed. Last: {last_err}")

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
        insights, used_model = call_openrouter(prompt, api_key)
        # Write JSON for dashboard
        OUT_JSON.write_text(json.dumps(insights, indent=2, ensure_ascii=False), encoding="utf-8")
        # Write markdown for humans
        md_lines = [f"# AI Cycling Insights — {datetime.date.today().isoformat()}  (via {used_model})", ""]
        md_lines.append(f"_Model: `{used_model}` via OpenRouter — auto-generated after daily Garmin sync._\n")
        for ins in insights:
            md_lines.append(f"### {ins['title']}")
            md_lines.append(f"{ins['description']}\n")
        OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"Wrote {OUT_JSON} and {OUT_MD} with 10 insights via {used_model}")
    except Exception as e:
        print(f"AI call failed: {e}")
        import traceback; traceback.print_exc()
        # Keep yesterday's insights if they exist — a stale-but-useful set beats
        # ten red error cards, and the dashboard's freshness badge still shows
        # the real date from the markdown header.
        prev = None
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        except Exception:
            prev = None
        prev_ok = isinstance(prev, list) and len(prev) == 10 and not str(
            (prev[0] or {}).get("title", "")
        ).lower().startswith("insight 1: ai")
        if prev_ok:
            print("Keeping previous successful insights (stale but valid).")
            prev_md = "\n".join(f"### {i.get('title')}\n{i.get('description', '')}\n" for i in prev)
            OUT_MD.write_text(
                "# AI Cycling Insights — showing last successful set\n\n"
                f"_AI generation failed {datetime.datetime.now().isoformat()}: {e}_\n\n"
                "The insights below are from the previous successful run. "
                "Garmin data in `garmin/data.json` is still fresh.\n\n" + prev_md,
                encoding="utf-8",
            )
            sys.exit(1)
        fallback = [
            {"title": f"Insight {i+1}: AI generation failed — using fallback", "description": f"Error: {e}. Check OPENROUTER_API_KEY and free-model quota. Your Garmin data is still fresh in garmin/data.json.", "badge": "bad", "badgeText": "Error"}
            for i in range(10)
        ]
        OUT_JSON.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
        OUT_MD.write_text(f"# AI Insights — generation failed {datetime.datetime.now().isoformat()}\n\nError: {e}\n\nCheck `OPENROUTER_API_KEY` secret and free-model quota.\n", encoding="utf-8")
        # Exit 1 so the workflow shows red and you notice
        sys.exit(1)

if __name__ == "__main__":
    main()
