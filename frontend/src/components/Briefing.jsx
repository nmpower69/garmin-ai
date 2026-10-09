import React from 'react'
import { motion, useReducedMotion } from 'framer-motion'

const row = (icon, time, html) => (
  <div className="tline">
    <span className="ttime">{icon} {time}</span>
    <div className="sub" style={{lineHeight:1.65}} dangerouslySetInnerHTML={{__html: html || '—'}}/>
  </div>
)

export default function Briefing({ briefing }){
  const reduced = useReducedMotion()
  if(!briefing){
    return (
      <div className="card shimmer-card"><div className="sub">Briefing brews with the morning sync…</div></div>
    )
  }
  const vc = briefing.verdict === 'ride' ? '#22c55e' : briefing.verdict === 'easy' ? '#f59e0b' : ACID_BORDER
  const bc = briefing.context || {}
  const badge = briefing.verdict === 'rest'
    ? <span className="badge rest">REST</span>
    : <span className={'badge ' + (briefing.verdict === 'ride' ? 'ok' : 'warn')}>{String(briefing.verdict).toUpperCase()}</span>

  const chips = [bc.rides_7d + ' rides / 7d', bc.km_7d + ' km / 7d', bc.days_since_ride + 'd since ride', bc.days_since_hard + 'd since hard', 'today: ' + bc.today]
  if(bc.acwr != null) chips.push('load ' + bc.acwr + 'x')

  const w = briefing.weather || {}
  const wParts = []
  if(w.temp_c != null) wParts.push(w.temp_c + '°C')
  if(w.humidity != null) wParts.push(w.humidity + '% RH')
  if(w.rain_pct != null) wParts.push('rain ' + w.rain_pct + '%')
  if(w.wind_kmh != null) wParts.push('wind ' + w.wind_kmh + ' km/h')
  const hasW = briefing.weather && (w.temp_c != null || w.rain_pct != null)

  const slb = briefing.sleep || {}
  const why = (
    <details className="card">
      <summary>Why this plan + sleep notes</summary>
      <ul style={{margin:'10px 0 0', paddingLeft:18, color:'var(--muted)', fontSize:13, lineHeight:1.7}}>
        {(briefing.reasons || []).map((r,i) => <li key={i}>{r}</li>)}
      </ul>
      <div className="sub" style={{marginTop:8}}>Usual bedtime: {slb.usual_bedtime} · Target: {slb.target}</div>
      {(slb.notes || []).map((x,i) => <div className="sub" key={i}>• {x}</div>)}
    </details>
  )

  let timeline
  if(briefing.ride){
    const r = briefing.ride, f = briefing.fuel || {}, sl = briefing.sleep || {}
    timeline = (
      <>
        <div className="card">
          <h3>Timeline</h3>
          {row('🌙','Tonight', ((f.pre||[])[0] || '') + '<br>Lights out <strong>' + sl.tonight + '</strong> (' + sl.target + ')')}
          {row('⏰','5:35 AM', ((f.pre||[])[1] || ''))}
          {row('🚴','6:20 AM', '<strong>' + r.title + '</strong> — ' + r.distance + '<br>' + r.pace)}
          {row('🧃','During', (f.during||[]).join('<br>'))}
          {row('🍽️','After', (f.post||[]).join('<br>'))}
        </div>
        <details className="card">
          <summary>Ride structure</summary>
          <div className="sub" style={{lineHeight:1.8, marginTop:8}}>Warmup: {r.warmup}<br/>Main: {r.main}<br/>Twist: {r.variation}<br/>Cooldown: {r.cooldown}</div>
        </details>
      </>
    )
  } else {
    const s2 = briefing.sleep || {}, f2 = briefing.fuel || {}
    timeline = (
      <>
        <div className="card">
          <h3>Rest-day timeline</h3>
          {row('😴','Tonight','Lights out <strong>' + s2.tonight + '</strong> (' + s2.target + ') — usual ' + s2.usual_bedtime)}
          {row('🚶','Tomorrow', ((briefing.rest_plan||[]).slice(0,2)).join('<br>'))}
          {row('🍽️','Fuel', ((f2.post||[]).slice(0,2)).join('<br>'))}
        </div>
        <details className="card">
          <summary>Full rest plan</summary>
          <div className="sub" style={{lineHeight:1.8, marginTop:8}} dangerouslySetInnerHTML={{__html:((briefing.rest_plan||[]).join('<br>'))}}/>
        </details>
      </>
    )
  }

  return (
    <motion.div className="briefing-grid"
      initial={reduced ? false : {opacity:0, y:30}}
      whileInView={reduced ? undefined : {opacity:1, y:0}}
      viewport={{once:true, amount:.1}}
      transition={{type:'spring', stiffness:90, damping:18}}>
      <div className="card" style={{borderLeft:'3px solid ' + vc}}>
        <div style={{display:'flex', gap:10, alignItems:'center', flexWrap:'wrap'}}>
          {badge}
          <strong style={{fontSize:19, fontFamily:'var(--font-display)'}}>{briefing.verdict_label}</strong>
        </div>
        <div className="zone-pills">{chips.map((x,i) => <span key={i}>{x}</span>)}</div>
        {hasW && <div className="sub" style={{marginTop:8}}>Kolhapur 5–9 AM: {wParts.join(' · ')}</div>}
        {(briefing.wx_notes||[]).map((n,i) => <div className="sub" key={i}>• {n}</div>)}
      </div>
      {timeline}
      {why}
    </motion.div>
  )
}

const ACID_BORDER = '#b7f542'
