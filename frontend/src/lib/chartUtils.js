import { Chart as ChartJS, registerables } from 'chart.js'

export { ChartJS }

export const ACID = '#b7f542'
export const MUTED = '#8a938a'
export const TEXT = '#f2f5ef'

ChartJS.register(...registerables)

ChartJS.defaults.font.family = "Inter,system-ui,sans-serif"
ChartJS.defaults.font.size = 11
ChartJS.defaults.color = MUTED
ChartJS.defaults.borderColor = 'rgba(255,255,255,.08)'
Object.assign(ChartJS.defaults.plugins.tooltip, {
  backgroundColor: '#0b0f0a',
  borderColor: 'rgba(183,245,66,.35)',
  borderWidth: 1,
  titleColor: '#f2f5ef',
  bodyColor: '#c8cfc4',
  padding: 10,
  cornerRadius: 10,
  displayColors: true,
})

export function setChartMotion(reduced){
  ChartJS.defaults.animation.duration = reduced ? 0 : 700
  ChartJS.defaults.animation.easing = 'easeOutQuart'
  ChartJS.defaults.elements.bar.hoverBorderWidth = 0
  ChartJS.defaults.elements.line.pointHoverRadius = 6
  ChartJS.defaults.elements.line.pointHoverBorderWidth = 2
  ChartJS.defaults.elements.point.hoverRadius = 6
}

// Soft neon glow behind a dataset. Attach to plugins; set dataset._glow = {color, blur}.
export function glowPlugin(){
  return { id:'glow', beforeDatasetDraw(c, args){
    const ds = c.data.datasets[args.index]
    const glow = ds && ds._glow
    if(!glow) return
    const {ctx} = c
    ctx.save()
    ctx.shadowColor = glow.color || 'rgba(183,245,66,.45)'
    ctx.shadowBlur = glow.blur || 14
  }, afterDatasetDraw(c, args){
    const ds = c.data.datasets[args.index]
    if(ds && ds._glow) c.ctx.restore()
  }}
}

export function areaGradient(canvas, c1, c2){
  try{
    const ctx = canvas.getContext('2d')
    const g = ctx.createLinearGradient(0,0,0,260)
    g.addColorStop(0, c1); g.addColorStop(1, c2)
    return g
  }catch(e){ return c1 }
}

// Shaded horizontal bands behind a chart (zones, thresholds)
export function bandPlugin(bands){
  return { id:'bands', beforeDatasetsDraw(c){
    const y = c.scales.y; if(!y) return
    const {ctx, chartArea:{left,right,top,bottom}} = c
    ctx.save()
    bands.forEach(b=>{
      const t1 = y.getPixelForValue(b.yMax), t2 = y.getPixelForValue(b.yMin)
      ctx.fillStyle = b.color
      ctx.fillRect(left, t1, right-left, t2-t1)
    })
    ctx.restore()
  }}
}

// Value labels above bars
export function barValuesPlugin(){
  return { id:'barValues', afterDatasetsDraw(c){
    const {ctx} = c
    ctx.save(); ctx.font = "700 10px Inter,system-ui"; ctx.fillStyle = 'rgba(242,245,239,.78)'; ctx.textAlign = 'center'
    c.data.datasets.forEach((ds,di)=>{
      if(ds.type==='line') return
      const meta = c.getDatasetMeta(di)
      meta.data.forEach((bar,i)=>{
        const v = ds.data[i]
        if(v==null||v===''||+v===0) return
        ctx.fillText(v, bar.x, bar.y - 5)
      })
    })
    ctx.restore()
  }}
}

// Center text inside doughnut charts. getLines(ctx, x, y) draws at center.
export function centerTextPlugin(getLines){
  return { id:'centerText', afterDraw(c){
    const meta = c.getDatasetMeta(0); if(!meta.data[0]) return
    const p = meta.data[0], ctx = c.ctx
    ctx.save(); ctx.textAlign = 'center'
    getLines(ctx, p.x, p.y)
    ctx.restore()
  }}
}
