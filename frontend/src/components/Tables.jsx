import React from 'react'
import { motion, useReducedMotion } from 'framer-motion'

const hrBadge = v => {
  if(typeof v !== 'number') return '—'
  const c = v < 139 ? 'ok' : v <= 159 ? 'ok' : v <= 167 ? 'warn' : 'bad'
  return <span className={'badge ' + c}>{v}</span>
}

const fade = (reduced, i) => reduced ? {} : ({
  initial:{opacity:0}, whileInView:{opacity:1},
  viewport:{once:true, amount:.1}, transition:{duration:.3, delay:Math.min(i*.03, .3)},
})

export function PowerTable({ model }){
  const reduced = useReducedMotion()
  const { powerRows, powerCols, powerBests } = model
  if(!powerRows.length){
    return (
      <div className="card"><div className="table-wrap"><table><tbody>
        <tr><td colSpan={11}><div className="empty-state">No power-meter rides in this window — table fills in when directPower data exists.</div></td></tr>
      </tbody></table></div></div>
    )
  }
  return (
    <div className="card"><div className="table-wrap"><table>
      <thead><tr>
        <th>Date</th><th>Ride</th>
        {powerCols.map(c => <th key={c} className="num">{c}</th>)}
        <th className="num">Avg W</th><th className="num">NP W</th><th className="num">Max W</th>
      </tr></thead>
      <tbody>
        {powerRows.map(({a, avg, max, np, p}, i) => (
          <motion.tr key={a.id || a.date} {...fade(reduced, i)}>
            <td data-label="Date">{a.date}</td><td data-label="Ride">{a.name.slice(0,18)}</td>
            {powerCols.map(c => {
              const v = p ? p[c] : null
              const pb = v != null && v === powerBests[c]
              return <td key={c} data-label={c} className={'num' + (pb ? ' pb' : '')}>{v ?? '—'}</td>
            })}
            <td data-label="Avg W" className="num">{avg}</td><td data-label="NP W" className="num">{np}</td><td data-label="Max W" className="num">{max}</td>
          </motion.tr>
        ))}
      </tbody>
    </table></div></div>
  )
}

export function LatestTable({ model }){
  const reduced = useReducedMotion()
  return (
    <div className="card"><div className="table-wrap"><table>
      <thead><tr>
        <th>Date</th><th>Name</th><th className="num">Dist</th><th>Time</th><th className="num">Elev</th>
        <th className="num">Avg HR</th><th className="num">Max HR</th><th className="num">Speed</th><th className="num">Cal</th>
      </tr></thead>
      <tbody>
        {model.latest.map((a, i) => (
          <motion.tr key={a.id || a.date} {...fade(reduced, i)}>
            <td data-label="Date">{a.date}</td><td data-label="Name">{a.name}</td>
            <td data-label="Dist" className="num">{a.distance_km} km</td><td data-label="Time">{a.duration}</td>
            <td data-label="Elev" className="num">{(typeof a.elevation_gain === 'number' ? a.elevation_gain : a._raw?.elevationGain) || '—'} m</td>
            <td data-label="Avg HR" className="num">{hrBadge(a.avg_hr)}</td>
            <td data-label="Max HR" className="num">{hrBadge(a.max_hr)}</td>
            <td data-label="Speed" className="num">{a.distance_km && a.duration_seconds ? (a.distance_km/(a.duration_seconds/3600)).toFixed(1) : '—'}</td>
            <td data-label="Cal" className="num">{a.calories}</td>
          </motion.tr>
        ))}
      </tbody>
    </table></div></div>
  )
}
