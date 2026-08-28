import os, json, pathlib
from garminconnect import Garmin
g=Garmin()
g.login(os.path.expanduser("~/.garminconnect"))
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
# fetch avg, NP, max for each
for aid in aids:
    splits = None
    try:
        act=g.get_activity(aid)
        # activity summary has more fields
        avg = act.get("averagePower") or act.get("avgPower") or act.get("averagePower") or None
        # also try details splits
        if avg is None:
            try:
                splits=g.get_activity_splits(aid)
            except: splits=None
            # splits may have lap avg
            if splits and "lapDTOs" in splits and splits["lapDTOs"]:
                avg = splits["lapDTOs"][0].get("averagePower")
        # get details for NP
        details=g.get_activity_details(aid)
        # NP not in details? Try activity's normalizedPower from splits or activity
        np_val = None
        maxp = act.get("maxPower") or act.get("maxPower") or None
        # Try to find NP in activity details vs splits
        if not maxp:
            # details may have maxPower in splits
            pass
        # Try to get NP from splits lap
        try:
            lap = splits["lapDTOs"][0]
            np_val = lap.get("normalizedPower")
            if avg is None:
                avg = lap.get("averagePower")
            if maxp is None:
                maxp = lap.get("maxPower")
        except: pass
        # Also try activity's power
        if avg is None:
            avg = act.get("averagePower")
        if np_val is None:
            np_val = act.get("normalizedPower")
        if maxp is None:
            maxp = act.get("maxPower")
        print(aid, "avg", avg, "np", np_val, "max", maxp)
        # Update pc
        if aid in pc["curves"]:
            pc["curves"][aid]["avgPower"] = avg
            pc["curves"][aid]["normalizedPower"] = np_val
            pc["curves"][aid]["maxPower"] = maxp
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
