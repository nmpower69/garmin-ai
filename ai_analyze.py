#!/usr/bin/env python3
"""
AI daily analysis for cycling — uses free OpenRouter models (see FALLBACK_MODELS)

Reads garmin/data.json + garmin/power_curves.json (fresh from sync)
Calls OpenRouter chat/completions and writes:
  - garmin/ai_insights.md  (human markdown, committed)
  - garmin/ai_insights.json (structured for dashboard)

Env required: OPENROUTER_API_KEY
Model: free OpenRouter models, tried in order (see MODEL / FALLBACK_MODELS)

Run locally:  OPENROUTER_API_KEY=sk-or-v1-... python ai_analyze.py
In GitHub Actions: secret OPENROUTER_API_KEY is injected.
"""
import json, os, sys, pathlib, datetime, textwrap, requests

MODEL = os.getenv("AI_MODEL")  # optional override, tried first
# Free models come and go on OpenRouter: on 2026-11-09 six of the seven
# previously hardcoded ":free" ids returned 404 ("no longer free" /
# "deprecated"), leaving only a 2.6B model. So the list is now discovered at
# run time from OpenRouter's public model catalogue. PREFERRED only sets the
# order among whatever is free *today*; anything else free follows, tiny and
# code-only models last. FALLBACK_MODELS is used only if discovery fails.
MODELS_URL = "https://openrouter.ai/api/v1/models"
PREFERRED = [
    "google/gemma-4-31b",
    "nvidia/nemotron-3-ultra",
    "nvidia/nemotron-3.5-super",
    "google/gemma-4-26b",
    "openrouter/free",          # OpenRouter's own router over free models
]
DEPRIORITIZED = ("liquid/", "-code", "1.7b", "2.6b", "nano")
MAX_ATTEMPTS = 6   # x 75 s timeout stays well inside the 15-min job limit
FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
    "liquid/lfm-2.5-2.6b:free",
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

def today_ist():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return datetime.date.today()


def build_zone_context(data, curves):
    """HR zones, FTP and VO2 max as plain lines, from data.json profile + power_curves.json."""
    profile = (data or {}).get("profile") or {}
    lines = []

    hz = profile.get("hr_zones") or {}
    f = hz.get("floors")
    if isinstance(f, list) and len(f) == 5:
        extra = ", ".join(x for x in (
            f"LTHR {hz['lthr']}" if hz.get("lthr") else "",
            f"max HR {hz['max_hr']}" if hz.get("max_hr") else "",
        ) if x)
        lines.append(f"HR zones (Garmin {hz.get('sport') or ''} {hz.get('method') or ''}{'; ' + extra if extra else ''}): "
                     f"Z1<{f[1]}, Z2 {f[1]}-{f[2]}, Z3 {f[2]}-{f[3]}, Z4 {f[3]}-{f[4]}, Z5>{f[4]} bpm.")
    else:
        lines.append("HR zones (from Garmin profile, not re-synced): LTHR 175, Max 196 => Z1<139, Z2 139-159, Z3 159-167, Z4 167-172, Z5>172 bpm.")

    ftp = (curves or {}).get("ftp")
    if isinstance(ftp, (int, float)) and ftp > 0:
        z = [round(ftp * k) for k in (0.55, 0.75, 0.90, 1.05, 1.20, 1.50)]
        lines.append(f"FTP {ftp} W — {(curves or {}).get('ftp_status') or ''}")
        lines.append(f"Power zones from that FTP: Z1<{z[0]}, Z2 {z[0]}-{z[1]}, Z3 {z[1]}-{z[2]}, Z4 {z[2]}-{z[3]}, Z5 {z[3]}-{z[4]}, Z6 {z[4]}-{z[5]}, Z7>{z[5]} W.")
    else:
        lines.append("FTP unknown — use HR zones.")

    vo2 = profile.get("vo2max") or {}
    daily = (data or {}).get("daily") or {}
    trend = [f"{d}: {v['vo2max_cycling']}" for d, v in sorted(daily.items()) if isinstance(v.get("vo2max_cycling"), (int, float))]
    for sport in ("cycling", "running"):
        v = vo2.get(sport)
        if isinstance(v, dict) and v.get("value"):
            lines.append(f"VO2 max ({sport}, Garmin): {v['value']} as of {v.get('date') or 'unknown date'}.")
    if trend:
        lines.append("Cycling VO2 max updates in window: " + ", ".join(trend[-10:]))
    if not any(isinstance(vo2.get(s), dict) and vo2[s].get("value") for s in ("cycling", "running")) and not trend:
        lines.append("VO2 max: Garmin has no estimate on file.")
    return "\n".join(lines)


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
    # Physiology from the latest sync (was a hardcoded string that said
    # "FTP 271W ... VO2max null" forever, whatever Garmin actually had).
    zones = build_zone_context(data, curves)

    prompt = f"""You are a cycling coach for a casual happiness rider, not a racer. The rider is a cyclist (outdoor road_biking), restarted after a break, never rides >3h, wants joyful improvement.

Context: Today is {today_ist().isoformat()}. Data window last 7-30 days. The rider is in Kolhapur, India, flat-rolling terrain.

Daily wellness last 7 days:
{chr(10).join(daily_lines)}

Recent rides last 7:
{chr(10).join(ride_lines)}

Best power per ride (real directPower rolling maxima):
{chr(10).join(curves_lines)}

Physiology (from Garmin, latest sync):
{zones}
Training readiness, HRV, RHR, sleep are volatile day to day — look at trends, not single days.

Task: Generate exactly 10 insights as JSON array. Each insight must have: "title" (short, like "Insight 1: Aug 12 ride breached easy ceiling by ~8 bpm") and "description" (2-3 sentences, plain English, specific to cycling, referencing actual dates/numbers above, actionable for happiness). Make them parallel to these running examples but cycling-appropriate:
Insight 1: Aug 20 run, breached easy ceiling by 3 beats.
Insight 2: Long run, vertical load is high for a road marathon block
Insight 3: Three-run day August 24 flags recovery sequencing risk
Insight 4: Marathon progression (now Cycling progression)
Insight 5: Zone model check (are the HR / power zones above still right for this rider?)
Insight 6: Polarized 80/20 (~80% of time easy, ~20% hard)
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
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c
        # Only fall back to the reasoning trace if it actually carries the array —
        # otherwise it's just the model thinking out loud.
        for p in (msg.get("reasoning"), msg.get("reasoning_content")):
            if isinstance(p, str) and "[" in p and "{" in p:
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


def _find_json_array(content):
    """Cut the JSON array out of a reply that wraps it in prose.

    Some free models answer "Here are your insights: [ ... ] Hope this helps".
    Returns the substring from the first '[{' to the last ']' (or the input).
    """
    import re
    s = content.strip()
    if s.startswith("["):
        return s
    m = re.search(r"\[\s*\{", s)
    if not m:
        return s
    end = s.rfind("]")
    return s[m.start():end + 1] if end > m.start() else s[m.start():]


def discover_free_models():
    """Today's free text models on OpenRouter, best first.

    Returns [(model_id, supports_reasoning_param)]. Falls back to the static
    FALLBACK_MODELS list if the public catalogue can't be fetched.
    """
    try:
        r = requests.get(MODELS_URL, timeout=20)
        r.raise_for_status()
        models = r.json().get("data") or []
    except Exception as e:
        print(f"Model discovery failed ({e}) — using static fallback list")
        return [(m, False) for m in FALLBACK_MODELS]

    free = []
    for m in models:
        mid = m.get("id") or ""
        if not (mid.endswith(":free") or mid == "openrouter/free"):
            continue
        p = m.get("pricing") or {}
        if str(p.get("prompt", "0")) not in ("0", "0.0") or str(p.get("completion", "0")) not in ("0", "0.0"):
            continue
        arch = m.get("architecture") or {}
        if "text" not in (arch.get("input_modalities") or ["text"]) or (arch.get("output_modalities") or ["text"]) != ["text"]:
            continue  # skip audio/image generators
        if (m.get("context_length") or 0) < 16000:
            continue
        free.append((mid, "reasoning" in (m.get("supported_parameters") or []), m.get("context_length") or 0))

    def rank(item):
        mid = item[0]
        pref = next((i for i, p in enumerate(PREFERRED) if mid.startswith(p)), len(PREFERRED))
        low = any(t in mid for t in DEPRIORITIZED)
        return (low, pref, -item[2])

    ordered = [(mid, rs) for mid, rs, _ in sorted(free, key=rank)]
    if not ordered:
        print("No free text models found in catalogue — using static fallback list")
        return [(m, False) for m in FALLBACK_MODELS]
    print("Free models today (in try order):", ", ".join(m for m, _ in ordered[:MAX_ATTEMPTS]))
    return ordered


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
    content = _find_json_array(content)
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
    candidates = discover_free_models()
    primary = model or MODEL
    if primary:
        candidates = [(primary, any(rs for m, rs in candidates if m == primary))] + [c for c in candidates if c[0] != primary]
    tried = candidates[:MAX_ATTEMPTS]
    last_err = None
    for mdl, supports_reasoning in tried:
        body = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": "You are a helpful cycling coach. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 6000,
        }
        if supports_reasoning:
            # Keep thinking short and out of the reply: on 2026-11-09 a reasoning
            # model spent its answer on "The user wants me to..." with no JSON.
            body["reasoning"] = {"effort": "low", "exclude": True}
        print(f"Calling OpenRouter {mdl}...")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=75)
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
        md_lines = [f"# AI Cycling Insights — {today_ist().isoformat()}  (via {used_model})", ""]
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
