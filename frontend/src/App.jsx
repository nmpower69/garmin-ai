import React, { useEffect, useMemo, useState } from 'react'
import { motion, useReducedMotion, useInView, animate } from 'framer-motion'
import Hero from './components/Hero.jsx'
import ChartCard from './components/ChartCard.jsx'
import Briefing from './components/Briefing.jsx'
import TrainingCalendar from './components/TrainingCalendar.jsx'
import RecordsGrid from './components/RecordsGrid.jsx'
import { PowerTable, LatestTable } from './components/Tables.jsx'
import { loadRequired, loadJson, loadText, buildModel } from './lib/data.js'
import {
  weeklyBarConfig, hrSpeedConfig, readinessConfig, hrvRhrConfig, vo2Config,
  ftpConfig, zoneDonutConfig, sleepConfig, recoveryConfig, flowConfig,
} from './lib/chartConfigs.js'
import { ACID } from './lib/chartUtils.js'

function garminFileUrl(dataUrl, file){
  return dataUrl ? dataUrl.replace(/data\.json$/, file) : '#'
}

function CountUp({ value, decimals = 0 }){
  const ref = React.useRef(null)
  const inView = useInView(ref, {once:true, amount:.5})
  const reduced = useReducedMotion()
  const [display, setDisplay] = useState(reduced ? value : 0)
  useEffect(() => {
    if(reduced){ setDisplay(value); return }
    if(!inView) return
    const controls = animate(0, value, {
      duration: 1.2, ease: [0.22, 1, 0.36, 1],
      onUpdate: v => setDisplay(v),
    })
    // Guarantee the true value even if rAF is throttled (background tabs, headless views)
    const guarantee = setTimeout(() => setDisplay(value), 1500)
    return () => { controls.stop(); clearTimeout(guarantee) }
  }, [inView, value, reduced])
  return <span ref={ref}>{display.toFixed(decimals)}</span>
}

function Kpi({ label, value, decimals, unit, sub, i }){
  const reduced = useReducedMotion()
  return (
    <motion.div className="card kpi-card"
      initial={reduced ? false : {opacity:0, y:30}}
      whileInView={reduced ? undefined : {opacity:1, y:0}}
      viewport={{once:true, amount:.3}}
      transition={{type:'spring', stiffness:100, damping:18, delay:(i%3)*.07}}>
      <div className="kpi-label">{label}</div>
      <div className="kpi">
        <CountUp value={value} decimals={decimals}/><span className="unit">{unit}</span>
      </div>
      <div className="kpi-sub">{sub}</div>
    </motion.div>
  )
}

function Section({ title, acid, sub, children, id }){
  const reduced = useReducedMotion()
  return (
    <section className="block" id={id}>
      <motion.h2 className="sec"
        initial={reduced ? false : {opacity:0, y:24}}
        whileInView={reduced ? undefined : {opacity:1, y:0}}
        viewport={{once:true, amount:.4}}
        transition={{type:'spring', stiffness:100, damping:18}}>
        {title} {acid && <span className="acid">{acid}</span>}
        {!reduced && (
          <motion.span className="sec-underline" aria-hidden="true"
            initial={{scaleX:0}}
            whileInView={{scaleX:1}}
            viewport={{once:true, amount:.4}}
            transition={{type:'spring', stiffness:60, damping:16, delay:.15}}/>
        )}
      </motion.h2>
      {sub && <p className="sec-sub">{sub}</p>}
      {children}
    </section>
  )
}

function StatusBand({ model, dataUrl }){
  const reduced = useReducedMotion()
  const chips = [
    {label:'Window', value: model.range.join(' → ')},
    {label:'Last sync', value: model.generatedAt ? new Date(model.generatedAt).toLocaleString() : '—'},
    {label:'AI Insights', value: model.aiLive ? 'Live' : 'Error', cls: model.aiLive ? 'live' : 'err'},
  ]
  return (
    <div className="wrap" style={{paddingTop:36}}>
      <motion.div className="status-band"
        initial={reduced ? false : {opacity:0, y:24}}
        whileInView={reduced ? undefined : {opacity:1, y:0}}
        viewport={{once:true, amount:.2}}
        transition={{type:'spring', stiffness:100, damping:18}}>
        {chips.map(c => (
          <div className="status-chip" key={c.label}>
            <div className="chip-label">{c.label}</div>
            <div className={'chip-value ' + (c.cls || '')}>{c.value}</div>
          </div>
        ))}
        <div className="status-chip">
          <div className="chip-label">Source</div>
          <div className="chip-value"><a href={dataUrl} target="_blank" rel="noreferrer">garmin/data.json</a></div>
        </div>
      </motion.div>
    </div>
  )
}

export default function App(){
  const [model, setModel] = useState(null)
  const [error, setError] = useState(null)
  const [dataUrl, setDataUrl] = useState(null)

  useEffect(() => {
    (async () => {
      try{
        const dataRes = await loadRequired('data.json')
        setDataUrl(dataRes.url)
        const [insightsRes, mdRes, powerRes, briefingRes] = await Promise.all([
          loadJson('ai_insights.json'), loadText('ai_insights.md'),
          loadJson('power_curves.json'), loadJson('tomorrow_briefing.json'),
        ])
        let aiModelName = 'OpenRouter'
        if(mdRes.payload){
          const m = mdRes.payload.match(/via\s+`([^`]+)`/)
          if(m) aiModelName = m[1]
        }
        setModel(buildModel(
          dataRes.payload,
          insightsRes.payload, aiModelName,
          powerRes.payload, briefingRes.payload,
          dataRes.url,
        ))
      }catch(e){ setError(e.message) }
    })()
  }, [])

  const charts = useMemo(() => {
    if(!model) return null
    return {
      weeklyVol: weeklyBarConfig(model, 'vol'),
      weeklyHrs: weeklyBarConfig(model, 'hrs'),
      weeklyVert: weeklyBarConfig(model, 'vert'),
      hrSpeed: hrSpeedConfig(model),
      readiness: readinessConfig(model),
      hrvRhr: hrvRhrConfig(model),
      vo2: vo2Config(),
      ftp: ftpConfig(model),
      zoneDonut: zoneDonutConfig(model),
      sleep: sleepConfig(model),
      recovery: recoveryConfig(model),
      flow: flowConfig(model),
    }
  }, [model])

  if(error) return <div className="load-error"><strong>Failed to load Garmin data.</strong><br/>{error}</div>

  return (
    <>
      <Hero totals={model?.totals}/>

      {model && <StatusBand model={model} dataUrl={dataUrl}/>}

      <div className="wrap">
        <Section title="Last" acid="30 days." sub="Every number comes straight from garmin/data.json — no manual entry, no vanity metrics." id="charts">
          <div className="grid">
            {(model?.kpis || []).map((k,i) => <Kpi key={k.label} {...k} i={i}/>)}
          </div>
        </Section>

        <Section title="Tomorrow's" acid="briefing." sub={model?.briefing ? 'Your plan for ' + model.briefing.for_date + ' — wake ' + (model.briefing.wake || '5:30 AM') + ', wheels ' + (model.briefing.ride_start || '6:20 AM') + '.' : 'Your plan for tomorrow — rebuilt every morning from your last 10 rides + recovery.'}>
          <Briefing briefing={model?.briefing}/>
        </Section>

        <Section title="Training" acid="calendar." sub="Acid depth = ride distance, dot = readiness tier. Click a day to open its note.">
          {model && <TrainingCalendar calendar={model.calendar} daily={model.daily} acts={model.acts}/>}
        </Section>

        <Section title="Intensity &" acid="sleep." sub="Where your hours went (rides bucketed by avg HR) and how you slept each night.">
          <div className="grid-3">
            <ChartCard title="Ride hours by HR zone" config={charts?.zoneDonut}
              sub="Whole rides bucketed by their avg HR — a ride counts fully in its average zone."
              wrapAfter={(
                <div className="zone-pills">
                  {(model?.zoneDef || []).map((z,i) => (
                    <span key={i}><i style={{display:'inline-block', width:9, height:9, borderRadius:'50%', background:z[3], marginRight:6}}/>{z[2]} · {(model?.zoneHrs?.[i] ?? 0).toFixed(1)}h</span>
                  ))}
                </div>
              )}/>
            <ChartCard title="Sleep stages per night (h)" config={charts?.sleep} span={2}
              sub="Stacked bars = deep / light / REM / awake · dashed amber = sleep score (right axis)."/>
          </div>
        </Section>

        <Section title="Recovery" acid="deep-dive." sub="Every recovery signal normalized to 0–100, composite in acid.">
          <ChartCard title="Recovery composite" config={charts?.recovery}
            sub="Composite = mean of available signals. HRV scaled 40–90, RHR inverted 60–40, stress inverted. Bands: green ≥75 · amber 50–75 · red <50."/>
        </Section>

        <Section title="Ride time:" acid="intensity × length." sub="Bar height = intensity bucket, segments = ride length. Hover for exact hours.">
          <ChartCard title="Hours by intensity × length" config={charts?.flow}
            sub={'Easy <139 bpm · Moderate 139–167 · Hard >167. Short <1h · Medium 1–2h · Long 2h+.' + (model ? ' Total ' + model.skTot.toFixed(1) + ' h.' + (model.skSkip > 0 ? ' (' + model.skSkip + ' ride(s) missing HR excluded.)' : '') : '')}/>
        </Section>

        <Section title="Records &" acid="streaks." sub="Bests in this window plus consistency.">
          {model && <RecordsGrid records={model.records}/>}
        </Section>

        <Section title="Weekly volume ·" acid="hours · vert." sub="Training load per calendar week — bars show totals, dashed line is the period average.">
          <div className="grid-3">
            <ChartCard title="Weekly distance (km)" config={charts?.weeklyVol}
              sub={model ? `Best week ${Math.max(0, ...(model.volData.length ? model.volData : [0]))} km · ${model.acts.length} rides total` : ''}/>
            <ChartCard title="Weekly hours" config={charts?.weeklyHrs}
              sub={model ? `Total ${model.totalHrs.toFixed(1)} h · avg ${(model.totalHrs/Math.max(1, model.weekLabels.length)).toFixed(1)} h/week` : ''}/>
            <ChartCard title="Weekly vert gain (m)" config={charts?.weeklyVert}
              sub={model ? `Total ${Math.round(model.totalElev)} m · hilliest week ${Math.max(0, ...(model.vertData.length ? model.vertData : [0]))} m` : ''}/>
          </div>
        </Section>

        <Section title="Heart rate & speed ·" acid="readiness · FTP." sub="Per-ride intensity with HR-zone shading, daily recovery state, and your power zones from FTP.">
          <div className="grid-3">
            <ChartCard title="Avg HR & speed per ride" config={charts?.hrSpeed}
              sub="Pink band = threshold (Z4+), green = easy (Z1). Dots = rides; line connects in date order."/>
            <ChartCard title="Training readiness (0–100)" config={charts?.readiness}
              sub="Green ≥ 75 ready · Amber 50–75 normal · Red < 50 rest. Dashed lines mark 50 / 75."/>
            <ChartCard title={'FTP ' + (model?.ftpVal ?? '') + 'W · power zones'} config={charts?.ftp}
              sub={(model?.ftpStatus ?? '') + ' Shading = zones behind the FTP line.'}
              wrapAfter={(
                <div className="zone-pills">
                  {model && [0,1,2,3,4,5].map(i => {
                    const zB = model.zB
                    const labels = [`Z1 <${zB[1]}`, `Z2 ${zB[1]}–${zB[2]}`, `Z3 ${zB[2]}–${zB[3]}`, `Z4 ${zB[3]}–${zB[4]}`, `Z5 ${zB[4]}–${zB[5]}`, `Z6+ >${zB[5]}`]
                    return <span key={i}>{labels[i]}W</span>
                  })}
                </div>
              )}/>
          </div>
        </Section>

        <Section title="VO2 max &" acid="HRV · RHR." sub="Garmin's VO2 estimate plus the HRV / resting-HR fatigue pair.">
          <div className="grid-3">
            <ChartCard title="VO2 max" config={charts?.vo2}
              sub="Garmin returns null — chart will populate when Garmin provides it."/>
            <ChartCard title="HRV & resting HR trend" config={charts?.hrvRhr} span={2}
              sub="Green = HRV (ms, left axis) with 7-day average dashed · Amber = resting HR (bpm, right axis). Falling HRV + rising RHR together = fatigue."/>
          </div>
        </Section>

        <Section title="Best power" acid="per ride." sub="Rolling bests from your power stream (10s neuromuscular · 5m VO2 · 20m ≈ FTP). Highlighted cell per column = personal best in this window. Avg/NP/Max are whole-ride values — never single-lap.">
          {model && <PowerTable model={model}/>}
        </Section>

        <Section title="Latest" acid="activities.">
          {model && <LatestTable model={model}/>}
        </Section>

        <Section title="What the" acid="coach says." sub="AI coaching notes generated after each morning sync.">
          {model?.aiLive && (
            <div className="card ai-header">
              <div className="sub">✨ <strong>AI-generated</strong> today via <code>{model.aiModelName}</code> on OpenRouter — from your fresh Garmin data.</div>
              <div className="sub">
                <a href={garminFileUrl(dataUrl, 'ai_insights.md')} target="_blank" rel="noreferrer">View garmin/ai_insights.md</a>
                {' • '}
                <a href={garminFileUrl(dataUrl, 'ai_insights.json')} target="_blank" rel="noreferrer">JSON</a>
              </div>
            </div>
          )}
          <div>
            {(model?.insights || []).map((n,i) => (
              <motion.div className="insight" key={i}
                initial={{opacity:0, x:-24}} whileInView={{opacity:1, x:0}}
                viewport={{once:true, amount:.3}}
                transition={{type:'spring', stiffness:110, damping:18, delay:Math.min(i*.05, .3)}}>
                <span className={'badge ' + (n.badge === 'ok' ? 'ok' : n.badge === 'warn' ? 'warn' : n.badge === 'bad' ? 'bad' : 'rest')}>{n.badgeText || n.badge || 'note'}</span>
                <strong>{n.title}</strong>
                <div className="body">{n.description || n.desc}</div>
              </motion.div>
            ))}
          </div>
        </Section>
      </div>

      <div className="footer">
        Generated from garmin/data.json · Casual cyclist &lt;3h · Happiness first · Next sync daily 10 AM IST
        <br/>Hero rider model: “Cyclist on bike” by firebird854 (Sketchfab), CC-BY-4.0
      </div>
    </>
  )
}
