import React, { useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'

const readinessColor = v => typeof v !== 'number' ? '#64748b' : v >= 75 ? '#22c55e' : v >= 50 ? '#f59e0b' : '#f87171'

export default function TrainingCalendar({ calendar, daily, acts }){
  const reduced = useReducedMotion()
  const { monthKeys, actsByDate, dataBase, winStart, winEnd } = calendar
  const [calYM, setCalYM] = useState(monthKeys.length ? monthKeys[monthKeys.length-1] : null)

  if(!calYM) return null
  const parts = calYM.split('-'); const Y = +parts[0], M = +parts[1]
  const first = new Date(Y, M-1, 1)
  const startDay = (first.getDay()+6)%7
  const dim = new Date(Y, M, 0).getDate()
  const title = first.toLocaleString(undefined, {month:'long', year:'numeric'})

  const days = []
  for(let i = 0; i < startDay; i++) days.push(null)
  for(let d = 1; d <= dim; d++){
    const key = Y + '-' + String(M).padStart(2,'0') + '-' + String(d).padStart(2,'0')
    const day = daily[key]; const rides = actsByDate[key] || []
    let km = 0; rides.forEach(a => { if(typeof a.distance_km === 'number') km += a.distance_km })
    const hasData = day || rides.length > 0
    const inten = Math.min(1, km/60)
    let missTip
    if(winStart && key < winStart) missTip = key + ' — before the synced window (' + winStart + ' → ' + winEnd + ')'
    else if(winEnd && key > winEnd) missTip = key + ' — not synced yet (sync window ends ' + winEnd + ')'
    else missTip = key + ' — no data for this day'
    const tip = !hasData ? missTip : (day
      ? (key + ' ride ' + km.toFixed(1) + 'km steps ' + day.steps + ' sleep ' + day.sleep_hours + ' HRV ' + day.hrv + ' RHR ' + day.resting_hr + ' readiness ' + day.training_readiness)
      : (key + ' ride ' + km.toFixed(1) + 'km'))
    const link = (day && dataBase) ? (dataBase + 'daily/' + key + '.md') : '#'
    days.push({key, d, km, hasData, tip, link, readiness: day?.training_readiness, steps: day?.steps, inten})
  }

  return (
    <div className="card">
      <div className="cal-head">
        <button className="cal-nav" disabled={calYM <= monthKeys[0]}
          onClick={() => setCalYM(monthKeys[monthKeys.indexOf(calYM)-1])}>‹</button>
        <strong>{title}</strong>
        <button className="cal-nav" disabled={calYM >= monthKeys[monthKeys.length-1]}
          onClick={() => setCalYM(monthKeys[monthKeys.indexOf(calYM)+1])}>›</button>
        <span className="sub">
          <span style={{color:'#22c55e'}}>●</span> ready ≥75 &nbsp;
          <span style={{color:'#f59e0b'}}>●</span> ok 50–75 &nbsp;
          <span style={{color:'#f87171'}}>●</span> tired &lt;50 &nbsp;
          <span style={{color:'#64748b'}}>●</span> no data
        </span>
      </div>
      <div className="cal-grid">
        {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d => <div className="cal-dow" key={d}>{d}</div>)}
        {days.map((day, i) => day === null ? <div key={'x'+i}/> : (
          <motion.a key={day.key}
            className={'cal-day' + (day.km > 0 ? ' ride' : (day.hasData ? ' rest' : ' nodata'))}
            style={day.km > 0 ? {background:`rgba(183,245,66,${(0.16+0.54*day.inten).toFixed(2)})`} : null}
            title={day.tip} href={day.link} target="_blank" rel="noreferrer"
            initial={reduced ? false : {opacity:0, scale:.92}}
            whileInView={reduced ? undefined : {opacity:1, scale:1}}
            viewport={{once:true, amount:.2}}
            transition={{duration:.25, delay:Math.min(i*.012, .3)}}>
            <span className="d">{day.d}</span>
            {day.km > 0
              ? <span className="km">{day.km.toFixed(0)} km</span>
              : (day.steps ? <span className="st">{(day.steps/1000).toFixed(1)}k</span> : null)}
            <span style={{display:'flex', justifyContent:'flex-end'}}>
              {day.readiness !== undefined && <span className="cal-dot" style={{background:readinessColor(day.readiness)}}/>}
            </span>
          </motion.a>
        ))}
      </div>
    </div>
  )
}
