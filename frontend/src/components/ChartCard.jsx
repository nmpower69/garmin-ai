import React, { useEffect, useRef } from 'react'
import { motion, useInView, useReducedMotion } from 'framer-motion'
import { ChartJS, setChartMotion } from '../lib/chartUtils'

// config: (canvas) => full Chart.js config object
export default function ChartCard({ title, sub, config, span, wrapAfter }){
  const ref = useRef(null)
  const wrapRef = useRef(null)
  const inView = useInView(wrapRef, {once:true, amount:.1})
  const reduced = useReducedMotion()

  useEffect(()=>{
    setChartMotion(reduced)
    if(!inView || !ref.current || !config) return
    const chart = new ChartJS(ref.current, config(ref.current))
    return () => chart.destroy()
  }, [inView, config, reduced])

  return (
    <motion.div className="card chart-card" ref={wrapRef}
      style={span ? {gridColumn:`span ${span}`} : null}
      initial={reduced ? false : {opacity:0, y:30}}
      whileInView={reduced ? undefined : {opacity:1, y:0}}
      viewport={{once:true, amount:.1}}
      transition={{type:'spring', stiffness:90, damping:18}}>
      <h3>{title}</h3>
      <canvas ref={ref} className="chart"/>
      {sub && <div className="chart-sub">{sub}</div>}
      {wrapAfter}
    </motion.div>
  )
}
