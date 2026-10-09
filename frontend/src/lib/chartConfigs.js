import { areaGradient, bandPlugin, barValuesPlugin, centerTextPlugin, glowPlugin, ACID } from './chartUtils'

// Each factory receives the canvas element (for gradients) and returns a full
// Chart.js config. All chart logic is ported 1:1 from the old dashboard.

export function weeklyBarConfig(model, kind){
  const arr = kind === 'vol' ? model.volData : kind === 'hrs' ? model.hrsData : model.vertData
  const color = kind === 'vol' ? ACID : kind === 'hrs' ? '#22c55e' : '#f59e0b'
  return (canvas) => ({
    type:'bar',
    data:{labels:model.weekLabels, datasets:[
      {label:kind, data:arr, backgroundColor:areaGradient(canvas, color+'ee', color+'33'), borderRadius:8, borderSkipped:false, maxBarThickness:44,
      hoverBackgroundColor:color, _glow:{color:'rgba(183,245,66,.30)', blur:12}},
      {label:'avg', data:model.avgLine(arr), type:'line', borderColor:'rgba(255,255,255,.7)', borderDash:[6,5], borderWidth:1.5, pointRadius:0, fill:false},
    ]},
    options:{
      plugins:{legend:{display:false}, tooltip:{callbacks:{afterBody:items => model.counts[items[0].dataIndex] + ' ride(s) that week'}}},
      scales:{x:{grid:{display:false}}, y:{beginAtZero:true, grid:{color:'rgba(255,255,255,.05)'}}},
    },
    plugins:[barValuesPlugin(), glowPlugin()],
  })
}

export function hrSpeedConfig(model){
  return (canvas) => ({
    type:'line',
    data:{labels:model.rideLabels, datasets:[
      {label:'Avg HR (bpm)', data:model.hrData, borderColor:'#f87171', backgroundColor:areaGradient(canvas,'rgba(248,113,113,.30)','rgba(248,113,113,0)'), fill:true, tension:.35, borderWidth:2.5, pointRadius:3.5, pointBackgroundColor:'#f87171', yAxisID:'y', _glow:{color:'rgba(248,113,113,.35)', blur:10}},
      {label:'Speed km/h', data:model.speedData, borderColor:ACID, tension:.35, borderWidth:2.5, pointRadius:3.5, pointBackgroundColor:ACID, yAxisID:'y1', _glow:{color:'rgba(183,245,66,.35)', blur:10}},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{labels:{boxWidth:12, usePointStyle:true}}},
      scales:{
        x:{grid:{display:false}},
        y:{type:'linear', position:'left', min:120, max:180, title:{display:true, text:'bpm', color:'#f87171'}},
        y1:{type:'linear', position:'right', min:15, max:40, grid:{drawOnChartArea:false}, title:{display:true, text:'km/h', color:ACID}},
      },
    },
    plugins:[glowPlugin(), bandPlugin([
      {yMin:167, yMax:180, color:'rgba(248,113,113,.10)'},
      {yMin:120, yMax:139, color:'rgba(183,245,66,.08)'},
    ])],
  })
}

export function readinessConfig(model){
  return () => ({
    type:'line',
    data:{labels:model.shortDates, datasets:[
      {label:'Readiness', data:model.readiness, borderColor:'#a78bfa', backgroundColor:'rgba(167,139,250,.20)', fill:true, tension:.35, borderWidth:2.5, pointRadius:3, spanGaps:true, _glow:{color:'rgba(167,139,250,.35)', blur:10},
       segment:{borderColor:c => { const v = c.p1?.parsed?.y ?? c.p1?.raw; if(v == null) return '#a78bfa'; return v < 50 ? '#f87171' : v < 75 ? '#f59e0b' : '#22c55e' }}},
      {label:'Rest (<50)', data:model.shortDates.map(() => 50), borderColor:'rgba(248,113,113,.6)', borderDash:[6,5], borderWidth:1, pointRadius:0, fill:false},
      {label:'Ready (75)', data:model.shortDates.map(() => 75), borderColor:'rgba(34,197,94,.6)', borderDash:[6,5], borderWidth:1, pointRadius:0, fill:false},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{labels:{boxWidth:12, usePointStyle:true}}},
      scales:{x:{grid:{display:false}}, y:{min:0, max:100, grid:{color:'rgba(255,255,255,.05)'}}},
    },
    plugins:[glowPlugin()],
  })
}

export function hrvRhrConfig(model){
  return (canvas) => ({
    type:'line',
    data:{labels:model.shortDates, datasets:[
      {label:'HRV', data:model.hrv, borderColor:'#22c55e', backgroundColor:areaGradient(canvas,'rgba(34,197,94,.25)','rgba(34,197,94,0)'), fill:true, tension:.35, borderWidth:2.5, pointRadius:2.5, yAxisID:'y', _glow:{color:'rgba(34,197,94,.30)', blur:10}},
      {label:'HRV 7d avg', data:model.ma7, borderColor:'#bbf7d0', borderDash:[6,5], borderWidth:1.5, pointRadius:0, tension:.4, yAxisID:'y'},
      {label:'RHR', data:model.rhr, borderColor:'#f59e0b', tension:.35, borderWidth:2.5, pointRadius:2.5, yAxisID:'y1'},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{labels:{boxWidth:12, usePointStyle:true}}},
      scales:{
        x:{grid:{display:false}},
        y:{position:'left', min:30, max:100, title:{display:true, text:'HRV', color:'#22c55e'}},
        y1:{position:'right', min:35, max:70, grid:{drawOnChartArea:false}, title:{display:true, text:'RHR bpm', color:'#f59e0b'}},
      },
    },
    plugins:[glowPlugin()],
  })
}

export function vo2Config(){
  return () => ({
    type:'line',
    data:{labels:[], datasets:[{data:[], borderColor:'#94a3b8', borderDash:[4,4]}]},
    options:{plugins:{legend:{display:false}}, scales:{x:{grid:{display:false}}, y:{min:30, max:60}}},
  })
}

export function ftpConfig(model){
  const zB = model.zB
  return () => ({
    type:'line',
    data:{labels:model.shortDates, datasets:[
      {label:`FTP ${model.ftpVal}W`, data:model.shortDates.map(() => model.ftpVal), borderColor:ACID, borderWidth:3, pointRadius:0, fill:false, _glow:{color:'rgba(183,245,66,.40)', blur:14}},
    ]},
    options:{
      plugins:{legend:{labels:{boxWidth:12}}},
      scales:{x:{grid:{display:false}}, y:{min:Math.max(100, zB[0]), max:Math.round(model.ftpVal*1.6), grid:{color:'rgba(255,255,255,.05)'}}},
    },
    plugins:[glowPlugin(), bandPlugin([
      {yMin:zB[0], yMax:zB[1], color:'rgba(148,163,184,.10)'},
      {yMin:zB[1], yMax:zB[2], color:'rgba(183,245,66,.10)'},
      {yMin:zB[2], yMax:zB[3], color:'rgba(34,197,94,.10)'},
      {yMin:zB[3], yMax:zB[4], color:'rgba(245,158,11,.12)'},
      {yMin:zB[4], yMax:zB[6], color:'rgba(248,113,113,.10)'},
    ])],
  })
}

export function zoneDonutConfig(model){
  return () => ({
    type:'doughnut',
    data:{labels:model.zoneDef.map(z => z[2]), datasets:[{
      data:model.zoneHrs.map(h => +h.toFixed(2)),
      backgroundColor:model.zoneDef.map(z => z[3]),
      borderColor:'#0b0f0a', borderWidth:3,
    }]},
    options:{cutout:'62%', hoverOffset:10, spacing:2, plugins:{legend:{display:false}}},
    plugins:[centerTextPlugin((ctx, x, y) => {
      ctx.fillStyle = ACID; ctx.font = '800 22px Space Grotesk, Inter, system-ui'
      ctx.fillText(model.zonedHrs.toFixed(1)+' h', x, y-2)
      ctx.fillStyle = '#8a938a'; ctx.font = '11px Inter, system-ui'
      ctx.fillText(model.acts.length+' rides', x, y+16)
    })],
  })
}

export function sleepConfig(model){
  return () => ({
    type:'bar',
    data:{labels:model.shortDates, datasets:[
      {label:'Deep', data:model.dates.map(d => model.slpH(d,'deepSleepSeconds')), backgroundColor:'#818cf8', borderRadius:3},
      {label:'Light', data:model.dates.map(d => model.slpH(d,'lightSleepSeconds')), backgroundColor:'#38bdf8', borderRadius:3},
      {label:'REM', data:model.dates.map(d => model.slpH(d,'remSleepSeconds')), backgroundColor:'#22d3ee', borderRadius:3},
      {label:'Awake', data:model.dates.map(d => model.slpH(d,'awakeSleepSeconds')), backgroundColor:'#475569', borderRadius:3},
      {label:'Score', data:model.dates.map(model.slpScore), type:'line', borderColor:'#fbbf24', borderDash:[5,4], borderWidth:2, pointRadius:2, yAxisID:'y1'},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{labels:{boxWidth:12, usePointStyle:true}}},
      scales:{
        x:{stacked:true, grid:{display:false}},
        y:{stacked:true, beginAtZero:true, title:{display:true, text:'hours'}, grid:{color:'rgba(255,255,255,.05)'}},
        y1:{position:'right', min:0, max:100, grid:{drawOnChartArea:false}, title:{display:true, text:'score'}},
      },
    },
  })
}

export function recoveryConfig(model){
  return (canvas) => ({
    type:'line',
    data:{labels:model.shortDates, datasets:[
      {label:'Composite', data:model.rcAll, borderColor:ACID, backgroundColor:areaGradient(canvas,'rgba(183,245,66,.30)','rgba(183,245,66,0)'), fill:true, tension:.35, borderWidth:3, pointRadius:3, spanGaps:true, _glow:{color:'rgba(183,245,66,.40)', blur:14}},
      {label:'Readiness', data:model.rcR, borderColor:'#a78bfa', tension:.3, borderWidth:1.2, pointRadius:0, spanGaps:true},
      {label:'HRV', data:model.rcH, borderColor:'#22c55e', tension:.3, borderWidth:1.2, pointRadius:0, spanGaps:true},
      {label:'RHR inv.', data:model.rcRh, borderColor:'#f59e0b', tension:.3, borderWidth:1.2, pointRadius:0, spanGaps:true},
      {label:'Battery', data:model.rcB, borderColor:'#38bdf8', tension:.3, borderWidth:1.2, pointRadius:0, spanGaps:true},
      {label:'Stress inv.', data:model.rcS, borderColor:'#f87171', tension:.3, borderWidth:1.2, pointRadius:0, spanGaps:true},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{labels:{boxWidth:12, usePointStyle:true}}},
      scales:{x:{grid:{display:false}}, y:{min:0, max:100, grid:{color:'rgba(255,255,255,.05)'}}},
    },
    plugins:[glowPlugin(), bandPlugin([
      {yMin:0, yMax:50, color:'rgba(248,113,113,.07)'},
      {yMin:50, yMax:75, color:'rgba(245,158,11,.07)'},
      {yMin:75, yMax:100, color:'rgba(34,197,94,.07)'},
    ])],
  })
}

export function flowConfig(model){
  const skM = model.skM
  return () => ({
    type:'bar',
    data:{labels:['Easy <139','Moderate 139-167','Hard >167'], datasets:[
      {label:'Short <1h', data:[+skM[0][0].toFixed(2), +skM[1][0].toFixed(2), +skM[2][0].toFixed(2)], backgroundColor:'#7dd3fc', borderRadius:6},
      {label:'Med 1-2h', data:[+skM[0][1].toFixed(2), +skM[1][1].toFixed(2), +skM[2][1].toFixed(2)], backgroundColor:'#38bdf8', borderRadius:6},
      {label:'Long 2h+', data:[+skM[0][2].toFixed(2), +skM[1][2].toFixed(2), +skM[2][2].toFixed(2)], backgroundColor:'#818cf8', borderRadius:6},
    ]},
    options:{
      interaction:{mode:'index', intersect:false},
      plugins:{
        legend:{labels:{boxWidth:12, usePointStyle:true}},
        tooltip:{callbacks:{footer:items => { let ft = 0; items.forEach(it => { ft += it.raw }); return 'Total ' + ft.toFixed(1) + ' h' }}},
      },
      scales:{
        x:{stacked:true, grid:{display:false}},
        y:{stacked:true, beginAtZero:true, title:{display:true, text:'hours'}, grid:{color:'rgba(255,255,255,.05)'}},
      },
    },
    plugins:[barValuesPlugin()],
  })
}
