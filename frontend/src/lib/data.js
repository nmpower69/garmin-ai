// All data loading + derivation, ported 1:1 from the old dashboard/index.html logic.

const devPath = f => new URL(`../garmin/${f}`, window.location.href).href
const prodPaths = f => ['/garmin-ai/garmin/' + f, import.meta.env.BASE_URL + 'garmin/' + f]

async function tryFetch(file, parse){
  const paths = [devPath(file), ...prodPaths(file)]
  let lastErr
  // Two passes over the path list with a short backoff — a dev-server proxy
  // connection can occasionally hang; a retry recovers without user-visible failure.
  for(let attempt = 0; attempt < 2; attempt++){
    for(const p of paths){
      try{
        const r = await fetch(p + (p.includes('?')?'&':'?') + `v=${Date.now()}`, {cache:'no-store'})
        if(r.ok) return { payload: await parse(r), url: p }
        lastErr = `${p} → ${r.status}`
      }catch(e){ lastErr = `${p} → ${e.message}` }
    }
    if(attempt === 0) await new Promise(res => setTimeout(res, 400))
  }
  return { error: lastErr }
}

export async function loadRequired(file){
  const res = await tryFetch(file, r => r.json())
  if(!res.payload) throw new Error(`Could not load garmin/${file} — tried several paths. Last: ${res.error}`)
  return res
}

export const loadJson = f => tryFetch(f, r => r.json())
export const loadText = f => tryFetch(f, r => r.text())

const num = v => typeof v === 'number' ? v : 0
const elevOf = a => num(a.elevation_gain ?? a._raw?.elevationGain)

export function buildModel(data, insights, aiModelName, powerCurves, briefing, dataUrl){
  const acts = (data.activities || []).filter(a => a.sport === 'road_biking').sort((a,b) => a.date.localeCompare(b.date))
  const daily = data.daily || {}

  // ---- KPIs (same 6 as old dashboard) ----
  const totalKm = acts.reduce((s,a) => s + num(a.distance_km), 0)
  const totalHrs = acts.reduce((s,a) => s + num(a.duration_seconds), 0) / 3600
  const totalElev = acts.reduce((s,a) => s + elevOf(a), 0)
  const avgSpeed = totalHrs ? totalKm/totalHrs : 0
  const hrs = acts.filter(a => typeof a.avg_hr === 'number').map(a => a.avg_hr)
  const avgHR = hrs.length ? hrs.reduce((a,b) => a+b, 0)/hrs.length : 0
  const best = acts.length ? Math.max(...acts.map(a => num(a.distance_km))) : 0
  const ftpVal = powerCurves?.ftp || 271
  const ftpStatus = powerCurves?.ftp_status || 'stale 2025-03-04 — retest needed'
  const kpis = acts.length === 0 ? [
    {label:'No rides', value:0, decimals:0, unit:'rides', sub:'rest week — check daily wellness'},
    {label:'FTP', value:ftpVal, decimals:0, unit:'W', sub:ftpStatus},
  ] : [
    {label:'Total Distance', value:totalKm, decimals:1, unit:'km', sub:`${acts.length} rides · longest ${best.toFixed(0)} km`},
    {label:'Total Time', value:totalHrs, decimals:1, unit:'h', sub:`avg ${(totalKm/acts.length).toFixed(1)} km / ride`},
    {label:'Avg Speed', value:avgSpeed, decimals:1, unit:'km/h', sub: avgSpeed < 25 ? 'easy conversational pace' : 'brisk pace for joy rider'},
    {label:'Vert Gain', value:totalElev, decimals:0, unit:'m', sub:`${(totalElev/Math.max(1,acts.length)).toFixed(0)} m / ride avg`},
    {label:'Avg HR', value:avgHR, decimals:0, unit:'bpm', sub: hrs.length ? (avgHR > 150 ? 'mostly threshold — ease off' : 'balanced base') : 'no HR data'},
    {label:'FTP', value:ftpVal, decimals:0, unit:'W', sub:ftpStatus},
  ]

  // ---- Weekly grouping (same formula as old dashboard) ----
  const weeks = {}
  acts.forEach(a => {
    const d = new Date(a.date)
    const year = d.getFullYear()
    const jan1 = new Date(year,0,1)
    const days = Math.floor((d - jan1)/86400000)
    const week = Math.ceil((days + jan1.getDay() + 1)/7)
    const key = `${year}-W${String(week).padStart(2,'0')}`
    if(!weeks[key]) weeks[key] = {km:0, sec:0, vert:0, count:0}
    weeks[key].km += num(a.distance_km)
    weeks[key].sec += num(a.duration_seconds)
    weeks[key].vert += elevOf(a)
    weeks[key].count++
  })
  const weekLabels = Object.keys(weeks).sort()
  const volData = weekLabels.map(k => +weeks[k].km.toFixed(1))
  const hrsData = weekLabels.map(k => +(weeks[k].sec/3600).toFixed(1))
  const vertData = weekLabels.map(k => weeks[k].vert)
  const counts = weekLabels.map(k => weeks[k].count)
  const avgLine = arr => { const m = arr.length ? arr.reduce((a,b) => a+b,0)/arr.length : 0; return arr.map(() => +m.toFixed(1)) }

  // ---- HR & Speed ----
  const rideLabels = acts.map(a => a.date.slice(5))
  const hrData = acts.map(a => a.avg_hr)
  const speedData = acts.map(a => a.distance_km && a.duration_seconds ? +(a.distance_km/(a.duration_seconds/3600)).toFixed(1) : null)

  // ---- Daily series ----
  const dates = Object.keys(daily).sort()
  const shortDates = dates.map(d => d.slice(5))
  const readiness = dates.map(d => typeof daily[d].training_readiness === 'number' ? daily[d].training_readiness : null)
  const hrv = dates.map(d => typeof daily[d].hrv === 'number' ? daily[d].hrv : null)
  const rhr = dates.map(d => typeof daily[d].resting_hr === 'number' ? daily[d].resting_hr : null)
  const ma7 = hrv.map((_,i) => {
    const w = hrv.slice(Math.max(0,i-6), i+1).filter(v => typeof v === 'number')
    return w.length >= 3 ? +(w.reduce((a,b) => a+b,0)/w.length).toFixed(1) : null
  })

  // ---- FTP zones ----
  const zB = [0, Math.round(ftpVal*0.55), Math.round(ftpVal*0.75), Math.round(ftpVal*0.90), Math.round(ftpVal*1.05), Math.round(ftpVal*1.20), Math.round(ftpVal*1.50)]

  // ---- Intensity donut (ride hours by avg-HR zone) ----
  const zoneDef = [[-1000,139,'Z1 Easy','#38bdf8'],[139,159,'Z2 Base','#22c55e'],[159,167,'Z3 Tempo','#a78bfa'],[167,172,'Z4 Threshold','#fb923c'],[172,10000,'Z5 Max','#f87171']]
  const zoneHrs = [0,0,0,0,0]; let zonedHrs = 0
  acts.forEach(a => {
    if(typeof a.avg_hr === 'number' && typeof a.duration_seconds === 'number'){
      for(let zi = 0; zi < zoneDef.length; zi++){
        if(a.avg_hr >= zoneDef[zi][0] && a.avg_hr < zoneDef[zi][1]){
          const h = a.duration_seconds/3600; zoneHrs[zi] += h; zonedHrs += h; break
        }
      }
    }
  })

  // ---- Sleep stack ----
  const slpH = (d, path) => {
    const D = daily[d]; const s2 = D && D._raw && D._raw.sleep && D._raw.sleep.dailySleepDTO
    const v = s2 ? s2[path] : null
    return typeof v === 'number' ? +(v/3600).toFixed(2) : 0
  }
  const slpScore = d => {
    const s2 = daily[d] ? daily[d].sleep_score : null
    let m = null
    if(typeof s2 === 'string') m = parseInt(s2,10)
    else if(typeof s2 === 'number') m = s2
    return (typeof m === 'number' && !isNaN(m)) ? m : null
  }

  // ---- Recovery composite ----
  const nReady = v => typeof v === 'number' ? v : null
  const nHrv = v => typeof v === 'number' ? Math.max(0,Math.min(100,(v-40)/50*100)) : null
  const nRhr = v => typeof v === 'number' ? Math.max(0,Math.min(100,(60-v)/20*100)) : null
  const nBb = v => typeof v === 'number' ? Math.max(0,Math.min(100,v)) : null
  const nStress = v => typeof v === 'number' ? Math.max(0,Math.min(100,100-v)) : null
  const rcAll = [], rcR = [], rcH = [], rcRh = [], rcB = [], rcS = []
  dates.forEach(d => {
    const D = daily[d] || {}
    const vals = [nReady(D.training_readiness), nHrv(D.hrv), nRhr(D.resting_hr), nBb(D.body_battery), nStress(D.stress)]
    rcR.push(vals[0]); rcH.push(vals[1]); rcRh.push(vals[2]); rcB.push(vals[3]); rcS.push(vals[4])
    const ok = vals.filter(v => v != null)
    rcAll.push(ok.length >= 3 ? +(ok.reduce((a,b) => a+b,0)/ok.length).toFixed(1) : null)
  })

  // ---- Flow: intensity x length ----
  const skM = [[0,0,0],[0,0,0],[0,0,0]]; let skSkip = 0
  acts.forEach(a => {
    if(typeof a.avg_hr === 'number' && typeof a.duration_seconds === 'number'){
      const zi = a.avg_hr < 139 ? 0 : (a.avg_hr <= 167 ? 1 : 2)
      const li = a.duration_seconds < 3600 ? 0 : (a.duration_seconds <= 7200 ? 1 : 2)
      skM[zi][li] += a.duration_seconds/3600
    } else { skSkip++ }
  })
  const zt = [skM[0][0]+skM[0][1]+skM[0][2], skM[1][0]+skM[1][1]+skM[1][2], skM[2][0]+skM[2][1]+skM[2][2]]
  const skTot = zt[0]+zt[1]+zt[2]

  // ---- Records & streaks ----
  let longest = null
  acts.forEach(a => { if(typeof a.distance_km === 'number' && (!longest || a.distance_km > (longest.distance_km||0))) longest = a })
  const wkBest = Object.entries(weeks).sort((a,b) => b[1].km - a[1].km)[0]
  const curves = (powerCurves && powerCurves.curves) ? powerCurves.curves : {}
  const pbest = k => { let b = 0; Object.values(curves).forEach(c => { if(c && typeof c[k] === 'number' && c[k] > b) b = c[k] }); return b }
  const b10 = pbest('10s'), b5 = pbest('300s'), b20 = pbest('1200s')
  const last14 = dates.slice(-14)
  const actsByDate = {}
  acts.forEach(a => { const k = a.date; if(!actsByDate[k]) actsByDate[k] = []; actsByDate[k].push(a) })
  const last14Dots = last14.map(d => ({date:d, has:(actsByDate[d]||[]).length > 0}))

  // ---- Power table ----
  const getCurve = id => powerCurves?.curves?.[id] || null
  const powerRows = acts.map(a => {
    const real = getCurve(String(a._raw?.activityId || a.id))
    const avg = real?.avgPower ?? a._raw?.averagePower ?? a._raw?.avgPower ?? 110
    const max = real?.maxPower ?? a._raw?.maxPower ?? 400
    const np = real?.normalizedPower ?? a._raw?.normalizedPower ?? Math.round(avg*1.3)
    const p = real ? { '10s':real['10s'], '30s':real['30s'], '60s':real['60s'], '5m':real['300s'], '20m':real['1200s'], '1h':real['3600s'] } : null
    return {a, avg, max, np, p}
  })
  const powerCols = ['10s','30s','60s','5m','20m','1h']
  const withP = powerRows.filter(r => r.p)
  const powerBests = {}
  powerCols.forEach(c => { const vs = withP.map(r => r.p[c] || 0); powerBests[c] = vs.length ? Math.max(...vs) : 0 })

  // ---- Latest activities ----
  const latest = [...acts].reverse().slice(0,10)

  // ---- Calendar ----
  const monthKeys = Array.from(new Set(Object.keys(daily).map(d => d.slice(0,7)))).sort()
  const dataBase = dataUrl ? dataUrl.replace(/data\.json$/,'') : ''
  const winStart = (data.date_range && data.date_range[0]) || null
  const winEnd = (data.date_range && data.date_range[1]) || null

  // ---- AI status (same heuristic as old dashboard) ----
  const first = (insights && insights[0] && insights[0].title || '').toLowerCase()
  const isFailed = first.includes('failed') || first.includes('not configured') || first.includes('error')
  const aiLive = Array.isArray(insights) && insights.length === 10 && !isFailed

  return {
    acts, daily, data,
    range: data.date_range || [],
    generatedAt: data.generated_at,
    totals: { km: totalKm.toFixed(0), hrs: totalHrs.toFixed(1), rides: acts.length, vert: Math.round(totalElev) },
    totalKm, totalHrs, totalElev, avgSpeed, avgHR,
    kpis, ftpVal, ftpStatus,
    weekLabels, volData, hrsData, vertData, counts, avgLine,
    rideLabels, hrData, speedData,
    dates, shortDates, readiness, hrv, rhr, ma7, zB,
    zoneDef, zoneHrs, zonedHrs,
    slpH, slpScore,
    rcAll, rcR, rcH, rcRh, rcB, rcS,
    skM, skTot, skSkip,
    records: { longest, wkBest, b10, b5, b20, last14Dots, rideCount: acts.length },
    powerRows, powerCols, powerBests, latest,
    calendar: { monthKeys, actsByDate, dataBase, winStart, winEnd },
    briefing,
    insights: Array.isArray(insights) ? insights : null,
    aiLive, aiModelName,
  }
}
