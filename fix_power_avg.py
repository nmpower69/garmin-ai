import os, json, pathlib
from garminconnect import Garmin
_tokenstore = os.path.expanduser("~/.garminconnect")
_email = os.getenv("GARMIN_EMAIL")
_password = os.getenv("GARMIN_PASSWORD")
# Pass credentials when available so garminconnect>=0.3.6 can self-heal from
# expired/poisoned cached tokens ("Failed to retrieve social profile").
# Without credentials, an expired token file is fatal in CI.
if _email and _password:
    g = Garmin(email=_email, password=_password)
else:
    g = Garmin()
g.login(_tokenstore)
# Dynamic: read all activity IDs from garmin/data.json (fresh from sync)
data_path = pathlib.Path("garmin/data.json")
try:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    aids = [str(a.get("id") or a.get("_raw",{}).get("activityId")) for a in data.get("activities",[]) if a.get("sport")=="road_biking"]
    aids = [a for a in aids if a and a != "None"]
    print(f"Found {len(aids)} cycling activities in data.json: {aids[-5:]}")
except Exception as e:
    print(f"Failed to load {data_path}: {e}")
    aids = []
pc_path = pathlib.Path("garmin/power_curves.json")
try:
    pc=json.load(open(pc_path))
except:
    pc={"computed_at": "", "ftp": 271, "ftp_status": "stale", "curves": {}}
pc.setdefault("curves", {})

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

def _coggan_np(powers, window=30):
    """Whole-ride Normalized Power (Coggan): 4th-power mean of rolling means.

    powers: watts series, zeros KEPT (coasting), Nones already removed.
    Assumes ~1Hz sampling so a 30-sample window ~= 30 seconds.
    """
    n = len(powers)
    if n < window:
        return None
    tot = 0.0
    cnt = 0
    run = sum(powers[:window])
    tot += (run / window) ** 4
    cnt += 1
    for i in range(window, n):
        run += powers[i] - powers[i - window]
        tot += (run / window) ** 4
        cnt += 1
    return (tot / cnt) ** 0.25 if cnt else None

# List-summary values from data.json (whole-ride, per Garmin's activity list)
raw_by_id = {}
try:
    for _a in data.get("activities", []):
        _raw = _a.get("_raw") if isinstance(_a, dict) else None
        if isinstance(_raw, dict):
            for _key in (str(_a.get("id") or ""), str(_raw.get("activityId") or "")):
                if _key and _key != "None":
                    raw_by_id[_key] = _raw
except Exception:
    pass

# fetch avg, NP, max for each — whole-ride values FIRST, single-lap LAST
# (lapDTOs[0] is one lap, not the ride: using it as ride NP understates
# variable rides, e.g. NP 139 for a ride whose true whole-ride NP is 171)
for aid in aids:
    splits = None
    try:
        act = g.get_activity(aid) or {}
        try:
            details = g.get_activity_details(aid)
        except Exception as e:
            print(aid, "details fail", e)
            details = None
        try:
            splits = g.get_activity_splits(aid)
        except Exception:
            splits = None
        lap0 = None
        try:
            if splits and isinstance(splits.get("lapDTOs"), list) and splits["lapDTOs"]:
                lap0 = splits["lapDTOs"][0]
                if len(splits["lapDTOs"]) > 1:
                    print(f"{aid}: {len(splits['lapDTOs'])} laps — ignoring lap values for ride totals, whole-ride only")
                    lap0 = None  # multi-lap: lap0 is NOT the ride, never use it
        except Exception:
            lap0 = None
        raw = raw_by_id.get(str(aid), {})
        # 1) Whole-ride values from detailed activity, then list summary
        avg = _num(act, "averagePower", "avgPower")
        if avg is None:
            avg = _num(raw, "averagePower", "avgPower")
        maxp = _num(act, "maxPower", "maximalPower")
        if maxp is None:
            maxp = _num(raw, "maxPower")
        np_val = _num(act, "normalizedPower", "normPower")
        if np_val is None:
            np_val = _num(raw, "normalizedPower")
        np_source = "activity" if np_val is not None else None
        # 2) Stream-computed whole-ride NP (Coggan) — independent of laps/keys
        try:
            if details:
                descs = {m["key"]: m["metricsIndex"] for m in details.get("metricDescriptors", [])}
                if "directPower" in descs:
                    p_idx = descs["directPower"]
                    powers = [m["metrics"][p_idx] for m in details.get("activityDetailMetrics", []) if m.get("metrics") and m["metrics"][p_idx] is not None]
                    powers = [float(p) for p in powers]
                    if powers:
                        if avg is None:
                            avg = round(sum(powers) / len(powers), 1)
                        cn = _coggan_np(powers)
                        if cn is not None:
                            if np_val is None:
                                np_val, np_source = round(cn, 1), "computed"
                            elif abs(cn - np_val) > 15:
                                print(f"{aid}: WARN NP sources disagree (activity {np_val} vs stream-computed {round(cn,1)}) — keeping activity value")
        except Exception as e:
            print(aid, "stream NP fail", e)
        # 3) Single-lap values ONLY for single-lap rides missing everything else
        if lap0:
            if avg is None:
                a2 = _num(lap0, "averagePower", "avgPower")
                if a2 is not None:
                    avg = a2
            if maxp is None:
                m2 = _num(lap0, "maxPower")
                if m2 is not None:
                    maxp = m2
            if np_val is None:
                n2 = _num(lap0, "normalizedPower")
                if n2 is not None:
                    np_val, np_source = n2, "lap(partial)"
        # Sanity: NP can never be below avg for the same effort
        if np_val is not None and avg is not None and np_val < avg - 1:
            print(f"{aid}: WARN inconsistent pair (avg {avg} / NP {np_val}) — mixed scopes, NP kept but flagged")
            np_source = (np_source or "unknown") + "+suspect"
        print(aid, "avg", avg, "np", np_val, "max", maxp, "src", np_source)
        # Update pc
        if aid in pc["curves"]:
            pc["curves"][aid]["avgPower"] = avg
            pc["curves"][aid]["normalizedPower"] = np_val
            pc["curves"][aid]["maxPower"] = maxp
            pc["curves"][aid]["npSource"] = np_source
            # Also update if _raw missing
        else:
            print("not in pc", aid)
    except Exception as e:
        print(aid, "fail", e)
        import traceback; traceback.print_exc()
# also need to handle reading back
# For aids where avg still None, try to compute avg from directPower average
for aid in aids:
    if pc["curves"].get(aid, {}).get("avgPower") is None:
        # compute avg from directPower
        try:
            d=g.get_activity_details(aid)
            descs={m["key"]:m["metricsIndex"] for m in d["metricDescriptors"]}
            if "directPower" not in descs:
                print(f"Skipping {aid} - no power meter data")
                continue
            p_idx=descs["directPower"]
            metrics=d["activityDetailMetrics"]
            powers=[m["metrics"][p_idx] for m in metrics if m["metrics"][p_idx] is not None]
            avg_calc = sum(powers)/len(powers) if powers else None
            if aid not in pc["curves"]:
                pc["curves"][aid] = {}
            pc["curves"][aid]["avgPower"] = round(avg_calc,1) if avg_calc else None
            print(f"computed avg for {aid}: {avg_calc}")
        except Exception as e:
            print("compute fail", aid, e)

# For any aid not in power_curves or missing 10s/30s curves, compute full rolling best power via directPower
for aid in aids:
    if aid not in pc["curves"] or "10s" not in pc["curves"].get(aid, {}):
        try:
            print(f"Computing full power curve for new activity {aid}...")
            d=g.get_activity_details(aid)
            descs={m["key"]:m["metricsIndex"] for m in d["metricDescriptors"]}
            if "directPower" not in descs or "directTimestamp" not in descs:
                print(f"Skipping {aid} - no power/timestamp data")
                continue
            p_idx=descs["directPower"]
            t_idx=descs["directTimestamp"]
            metrics=d["activityDetailMetrics"]
            powers=[]
            times=[]
            for m in metrics:
                arr=m["metrics"]
                p=arr[p_idx]
                t=arr[t_idx]
                if p is not None and t is not None:
                    powers.append(float(p))
                    times.append(float(t))
            if not powers:
                print(f"No power data for {aid}")
                continue
            t0=times[0]
            rel=[(t-t0)/1000 for t in times]
            windows=[10,30,60,300,1200,3600]
            results={}
            for w in windows:
                best=0
                j=0
                s=0
                for i in range(len(powers)):
                    while j < len(powers) and rel[j]-rel[i] <= w:
                        s+=powers[j]
                        j+=1
                    cnt=j-i
                    if cnt>0:
                        avg=s/cnt
                        if avg>best:
                            best=avg
                    s-=powers[i]
                    if j<=i:
                        j=i+1
                results[w]=round(best)
            # Get date from data.json
            date = next((a["date"] for a in data.get("activities",[]) if str(a.get("id"))==aid), "unknown")
            pc["curves"][aid] = {
                "date": date,
                "10s": results[10],
                "30s": results[30],
                "60s": results[60],
                "300s": results[300],
                "1200s": results[1200],
                "3600s": results[3600],
                "avgPower": pc["curves"].get(aid, {}).get("avgPower"),
                "normalizedPower": pc["curves"].get(aid, {}).get("normalizedPower"),
                "maxPower": pc["curves"].get(aid, {}).get("maxPower"),
            }
            print(f"New curve {aid} {date}: {results}")
        except Exception as e:
            print(f"Failed to compute curve for {aid}: {e}")
            import traceback; traceback.print_exc()

json.dump(pc, open(pc_path,"w"), indent=2)
print("updated", pc_path)
print(json.dumps(pc, indent=2)[:2000])
