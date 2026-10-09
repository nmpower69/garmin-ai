import React from 'react'
import { motion, useReducedMotion } from 'framer-motion'

export default function RecordsGrid({ records }){
  const reduced = useReducedMotion()
  const { longest, wkBest, b10, b5, b20, last14Dots, rideCount } = records
  const cards = []
  if(longest) cards.push({label:'Longest ride', value:longest.distance_km.toFixed(0)+' km', sub:longest.date + ' · ' + longest.name, icon:'🏁'})
  if(wkBest) cards.push({label:'Biggest week', value:wkBest[1].km.toFixed(0)+' km', sub:wkBest[0] + ' · ' + wkBest[1].count + ' rides', icon:'📈'})
  if(b10 > 0) cards.push({label:'Best 10s power', value:b10+' W', sub:'neuromuscular', icon:'⚡'})
  if(b5 > 0) cards.push({label:'Best 5m power', value:b5+' W', sub:'VO2', icon:'🔥'})
  if(b20 > 0) cards.push({label:'Best 20m power', value:b20+' W', sub:'approx FTP', icon:'⏱️'})
  cards.push({label:'Last 14 days', value:rideCount+' rides / 30d', dots:last14Dots, icon:'🗓️'})

  return (
    <div className="grid">
      {cards.map((r,i) => (
        <motion.div className="card kpi-card" key={r.label}
          initial={reduced ? false : {opacity:0, y:30}}
          whileInView={reduced ? undefined : {opacity:1, y:0}}
          viewport={{once:true, amount:.3}}
          transition={{type:'spring', stiffness:100, damping:18, delay:(i%3)*.07}}>
          <div className="kpi-icon">{r.icon}</div>
          <div>
            <div className="kpi-label">{r.label}</div>
            <div className="kpi" style={{fontSize:26}}>{r.value}</div>
            {r.dots
              ? <div style={{marginTop:8}}>{r.dots.map(d => (
                  <span key={d.date} title={d.date + (d.has ? ' — ride' : '')}
                    style={{display:'inline-block', width:14, height:14, borderRadius:'50%',
                      background:d.has ? 'var(--acid)' : 'rgba(255,255,255,.12)', marginRight:5,
                      border:'1px solid rgba(255,255,255,.2)', verticalAlign:'middle'}}/>
                ))}</div>
              : <div className="kpi-sub">{r.sub}</div>}
          </div>
        </motion.div>
      ))}
    </div>
  )
}
