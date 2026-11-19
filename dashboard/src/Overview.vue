<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Activity, BellRing, CalendarDays, ChevronLeft, ChevronRight,
  Download, FileText, ListChecks, MapPin, PieChart, Search, SearchX } from 'lucide-vue-next'
import { dateLabel, eventNames, formatDMY, num, useWorkspace } from './workspace'
import TrendChart from './TrendChart.vue'
import ProfileAvatar from './ProfileAvatar.vue'
defineProps({activityOnly:Boolean})
const {s,refresh}=useWorkspace()

const pieColors=['#0353a4','#127e72','#d96b43','#6366f1','#e0a265','#2c8c99','#8b5cf6','#64748b']
const periods=[['daily','Daily'],['weekly','Weekly'],['monthly','Monthly']]
const trendPeriod=ref('daily'), trendRegion=ref(''), trendDealer=ref('')

const panels=reactive({
  checkin:{period:'daily',groupBy:'region',region:'',dealer:''},
  miss:{period:'daily',groupBy:'region',region:'',dealer:''},
  al:{period:'daily',groupBy:'region',region:'',dealer:''},
})

const scopeRows=computed(()=>s.metrics?.daily_scope||[])
const allRegions=computed(()=>{
  const regs=new Set([
    ...(s.data?.roster||[]).map(r=>r.region),
    ...scopeRows.value.map(r=>r.region),
  ].filter(Boolean))
  return [...regs].sort()
})
function dealersFor(reg){
  const dlrs=new Set()
  for(const r of (s.data?.roster||[])) if((!reg||r.region===reg)&&r.dealer) dlrs.add(r.dealer)
  for(const r of scopeRows.value) if((!reg||r.region===reg)&&r.dealer&&r.dealer!=='Unassigned') dlrs.add(r.dealer)
  return [...dlrs].sort()
}
const trendDealers=computed(()=>dealersFor(trendRegion.value))
watch(trendRegion,()=>{if(trendDealer.value&&!trendDealers.value.includes(trendDealer.value))trendDealer.value=''})

function bucketKey(dateStr,period){
  if(!dateStr)return ''
  if(period==='monthly')return dateStr.slice(0,7)
  if(period==='weekly'){
    const d=new Date(dateStr+'T12:00:00')
    if(Number.isNaN(d.getTime()))return dateStr
    const dow=(d.getDay()+6)%7
    d.setDate(d.getDate()-dow)
    return d.toISOString().slice(0,10)
  }
  return dateStr
}
function bucketLabel(key,period){
  if(!key)return ''
  if(period==='monthly')return dateLabel(key+'-01',{month:'short',year:'numeric'})
  if(period==='weekly')return 'Wk '+dateLabel(key,{month:'short',day:'numeric'})
  return dateLabel(key,{month:'short',day:'numeric'})
}

const trendSeries=computed(()=>{
  const baseSeries=s.metrics?.series||[]
  const buckets=new Map()
  for(const d of baseSeries){
    const key=bucketKey(d.date,trendPeriod.value)
    if(key&&!buckets.has(key))buckets.set(key,{date:key,label:bucketLabel(key,trendPeriod.value),pins:0,alerts:0,al:0,reports:0})
  }
  const hasFilter=Boolean(trendRegion.value||trendDealer.value)
  if(!hasFilter&&!scopeRows.value.length){
    for(const d of baseSeries){
      const key=bucketKey(d.date,trendPeriod.value)
      const b=buckets.get(key)
      if(!b)continue
      b.pins+=d.pins||0; b.alerts+=d.alerts||0; b.al+=d.al||0; b.reports+=d.reports||0
    }
  }else if(!hasFilter){
    for(const d of baseSeries){
      const key=bucketKey(d.date,trendPeriod.value)
      const b=buckets.get(key)
      if(!b)continue
      b.pins+=d.pins||0; b.alerts+=d.alerts||0; b.al+=d.al||0; b.reports+=d.reports||0
    }
  }else{
    for(const row of scopeRows.value){
      if(trendRegion.value&&row.region!==trendRegion.value)continue
      if(trendDealer.value&&row.dealer!==trendDealer.value)continue
      const key=bucketKey(row.date,trendPeriod.value)
      let b=buckets.get(key)
      if(!b){b={date:key,label:bucketLabel(key,trendPeriod.value),pins:0,alerts:0,al:0,reports:0};buckets.set(key,b)}
      b.pins+=row.checkin||0; b.alerts+=row.miss||0; b.al+=row.al||0; b.reports+=row.reports||0
    }
  }
  return [...buckets.values()].sort((a,b)=>a.date.localeCompare(b.date))
})

const trendLines=[
  {key:'pins',label:'Check In',color:'#0353a4',fill:true},
  {key:'alerts',label:'Miss Report',color:'#d96b43'},
  {key:'al',label:'Annual Leave (AL)',color:'#6366f1',dashed:true},
]

const mixPie=computed(()=>{
  const sums=trendSeries.value.reduce((a,b)=>({checkin:a.checkin+b.pins,miss:a.miss+b.alerts,al:a.al+b.al}),{checkin:0,miss:0,al:0})
  const items=[
    {label:'Check In',value:sums.checkin,color:'#0353a4'},
    {label:'Miss Report',value:sums.miss,color:'#d96b43'},
    {label:'Annual Leave (AL)',value:sums.al,color:'#6366f1'},
  ].filter(x=>x.value>0)
  const total=items.reduce((n,x)=>n+x.value,0)
  let offset=0
  const slices=items.map(x=>{
    const pct=total?(x.value/total)*100:0
    const slice={...x,from:offset,pct,pctLabel:Math.round(pct*10)/10}
    offset+=pct
    return slice
  })
  return {total,slices,sums}
})

function buildPerformance(metricKey){
  const cfg=panels[metricKey]
  const byScope=new Map()
  const byBucketScope=new Map()
  let total=0
  for(const row of scopeRows.value){
    if(cfg.region&&row.region!==cfg.region)continue
    if(cfg.dealer&&row.dealer!==cfg.dealer)continue
    const val=Number(row[metricKey]||0)
    if(!val)continue
    total+=val
    const scopeName=cfg.groupBy==='dealer'?(row.dealer?`${row.dealer} (${row.region})`:row.region):(row.region||'Unknown')
    byScope.set(scopeName,(byScope.get(scopeName)||0)+val)
    const bKey=bucketKey(row.date,cfg.period)
    const bsKey=bKey+'|'+scopeName
    if(!byBucketScope.has(bsKey))byBucketScope.set(bsKey,{bucket:bKey,label:bucketLabel(bKey,cfg.period),scope:scopeName,value:0})
    byBucketScope.get(bsKey).value+=val
  }
  const sortedScopes=[...byScope.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))
  const top=sortedScopes.slice(0,7)
  const otherVal=sortedScopes.slice(7).reduce((n,[,v])=>n+v,0)
  if(otherVal>0)top.push(['Other',otherVal])
  let offset=0
  const slices=top.map(([label,value],i)=>{
    const pct=total?(value/total)*100:0
    const slice={label,value,from:offset,pct,pctLabel:Math.round(pct*10)/10,color:pieColors[i%pieColors.length]}
    offset+=pct
    return slice
  })
  const table=[...byBucketScope.values()]
    .sort((a,b)=>b.bucket.localeCompare(a.bucket)||b.value-a.value||a.scope.localeCompare(b.scope))
    .map(r=>({...r,share:total?Math.round((r.value/total)*1000)/10:0}))
  return {total,slices,table}
}

const perfSections=computed(()=>[
  {key:'checkin',title:'Performance Check In',subtitle:'Location check-ins by Region & Dealer',valueLabel:'Check In',icon:MapPin,color:'#0353a4',data:buildPerformance('checkin')},
  {key:'miss',title:'Performance Miss Report',subtitle:'Missing report alerts by Region & Dealer',valueLabel:'Miss Report',icon:BellRing,color:'#d96b43',data:buildPerformance('miss')},
  {key:'al',title:'Performance AL',subtitle:'Annual Leave by Region & Dealer',valueLabel:'Total AL',icon:CalendarDays,color:'#6366f1',data:buildPerformance('al')},
])

const regFilter=ref(''),dealerFilter=ref(''),saSearch=ref(''),summaryPage=ref(1)
const summaryRegions=computed(()=>[...new Set((s.overviewRows||[]).map(r=>r.region).filter(Boolean))].sort())
const summaryDealers=computed(()=>[...new Set((s.overviewRows||[]).filter(r=>!regFilter.value||r.region===regFilter.value).map(r=>r.dealer).filter(Boolean))].sort())
watch(regFilter,()=>{if(dealerFilter.value&&!summaryDealers.value.includes(dealerFilter.value))dealerFilter.value=''})
const filteredSummary=computed(()=>{
  const term=saSearch.value.trim().toLocaleLowerCase()
  return (s.overviewRows||[]).filter(r=>(!regFilter.value||r.region===regFilter.value)
    &&(!dealerFilter.value||r.dealer===dealerFilter.value)
    &&(!term||[r.name,r.telegram_id,r.region,r.dealer,r.controller,r.group].some(v=>String(v||'').toLocaleLowerCase().includes(term))))
})
const summaryPages=computed(()=>Math.max(1,Math.ceil(filteredSummary.value.length/12)))
const visibleSummary=computed(()=>filteredSummary.value.slice((summaryPage.value-1)*12,summaryPage.value*12))
watch([regFilter,dealerFilter,saSearch,()=>s.overviewRows],()=>{summaryPage.value=1})
function exportOverviewCsv(){
  const header=['SA Name','Telegram ID','Region / Dealer','Controller','Total Visit','Total Miss']
  const lines=[header,...filteredSummary.value.map(r=>[r.name,r.telegram_id,`${r.region} / ${r.dealer||'—'}`,r.controller||'—',r.total_visit,r.total_miss])]
  const csv='\uFEFF'+lines.map(line=>line.map(value=>'"'+String(value??'').replaceAll('"','""')+'"').join(',')).join('\r\n')
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a')
  a.href=url;a.download=`overview-sa-summary-${s.fromDate}-to-${s.toDate}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
const icons={reports:FileText,pins:MapPin,alerts:BellRing}
async function filter(k){s.kind=k;s.page=1;await refresh()}
async function turn(n){s.page=n;await refresh()}
</script>
<template>
  <template v-if="!activityOnly">
    <section class="panel analyst-toolbar">
      <div class="analyst-title"><ListChecks :size="18"/><div><h2>Performance Analyst</h2><p>{{formatDMY(s.fromDate)}} – {{formatDMY(s.toDate)}} · Check In, Miss Report &amp; Annual Leave</p></div></div>
      <div class="analyst-controls">
        <select v-model="trendRegion" class="control small-control" aria-label="Filter analyst by region"><option value="">All regions</option><option v-for="r in allRegions" :key="r">{{r}}</option></select>
        <select v-model="trendDealer" class="control small-control" aria-label="Filter analyst by dealer"><option value="">All dealers</option><option v-for="d in trendDealers" :key="d">{{d}}</option></select>
        <div class="period-tabs" role="group" aria-label="Trend period">
          <button v-for="[k,label] in periods" :key="k" type="button" :class="{active:trendPeriod===k}" @click="trendPeriod=k">{{label}}</button>
        </div>
      </div>
    </section>

    <div class="analyst-top-grid">
      <section class="panel trend">
        <div class="panel-head">
          <div><h2>Activity Trend ({{periods.find(p=>p[0]===trendPeriod)?.[1]}})</h2><p>Check In, Miss Report &amp; AL line chart</p></div>
          <div class="period-tabs" role="group" aria-label="Line chart period">
            <button v-for="[k,label] in periods" :key="k" type="button" :class="{active:trendPeriod===k}" @click="trendPeriod=k">{{label}}</button>
          </div>
        </div>
        <div class="chart-legend">
          <span v-for="l in trendLines" :key="l.key"><i :style="{borderTopColor:l.color,borderTopStyle:l.dashed?'dashed':'solid',borderTopWidth:'3px'}"></i>{{l.label}}</span>
        </div>
        <TrendChart :series="trendSeries" :lines="trendLines"/>
      </section>

      <section class="panel coverage">
        <div class="panel-head"><div><h2>Overall Mix · Pie Chart</h2><p>Share of Check In, Miss Report &amp; AL</p></div><PieChart :size="18" class="quiet"/></div>
        <div class="pie-body">
          <svg viewBox="0 0 180 180" class="pie-svg" role="img" aria-label="Activity share pie chart">
            <circle cx="90" cy="90" r="80" fill="#eef3f1"/>
            <circle v-for="sl in mixPie.slices" :key="sl.label" cx="90" cy="90" r="40" fill="none" :stroke="sl.color" stroke-width="80" pathLength="100"
              :stroke-dasharray="Math.max(0.2,sl.pct)+' '+(100-Math.max(0.2,sl.pct))" :stroke-dashoffset="-sl.from" transform="rotate(-90 90 90)">
              <title>{{sl.label}}: {{num(sl.value)}} ({{sl.pctLabel}}%)</title>
            </circle>
          </svg>
          <div class="pie-legend">
            <div v-for="sl in mixPie.slices" :key="sl.label" class="pie-legend-row"><i :style="{background:sl.color}"></i><span>{{sl.label}}</span><b>{{num(sl.value)}} ({{sl.pctLabel}}%)</b></div>
            <p v-if="!mixPie.slices.length" class="quiet" style="font-size:12px">No records in selected dates</p>
          </div>
        </div>
      </section>
    </div>

    <div class="perf-cards-grid">
      <section v-for="sec in perfSections" :key="sec.key" class="panel perf-card">
        <div class="panel-head perf-head">
          <div><h2>{{sec.title}}</h2><p>{{sec.subtitle}}</p></div>
          <strong class="perf-total" :style="{color:sec.color}">{{num(sec.data.total)}}</strong>
        </div>
        <div class="perf-toolbar">
          <div class="period-tabs small" role="group" :aria-label="sec.title+' period'">
            <button v-for="[k,label] in periods" :key="k" type="button" :class="{active:panels[sec.key].period===k}" @click="panels[sec.key].period=k">{{label}}</button>
          </div>
          <div class="scope-tabs" role="group" :aria-label="sec.title+' grouping'">
            <button type="button" :class="{active:panels[sec.key].groupBy==='region'}" @click="panels[sec.key].groupBy='region'">Region</button>
            <button type="button" :class="{active:panels[sec.key].groupBy==='dealer'}" @click="panels[sec.key].groupBy='dealer'">Dealer</button>
          </div>
          <select v-model="panels[sec.key].region" class="control small-control" :aria-label="sec.title+' region'" @change="panels[sec.key].dealer=''">
            <option value="">All regions</option><option v-for="r in allRegions" :key="r">{{r}}</option>
          </select>
          <select v-model="panels[sec.key].dealer" class="control small-control" :aria-label="sec.title+' dealer'">
            <option value="">All dealers</option><option v-for="d in dealersFor(panels[sec.key].region)" :key="d">{{d}}</option>
          </select>
        </div>
        <div class="pie-body compact">
          <svg viewBox="0 0 180 180" class="pie-svg small" role="img" :aria-label="sec.title+' pie chart'">
            <circle cx="90" cy="90" r="80" fill="#eef3f1"/>
            <circle v-for="sl in sec.data.slices" :key="sl.label" cx="90" cy="90" r="40" fill="none" :stroke="sl.color" stroke-width="80" pathLength="100"
              :stroke-dasharray="Math.max(0.2,sl.pct)+' '+(100-Math.max(0.2,sl.pct))" :stroke-dashoffset="-sl.from" transform="rotate(-90 90 90)">
              <title>{{sl.label}}: {{num(sl.value)}} ({{sl.pctLabel}}%)</title>
            </circle>
          </svg>
          <div class="pie-legend">
            <div v-for="sl in sec.data.slices" :key="sl.label" class="pie-legend-row"><i :style="{background:sl.color}"></i><span>{{sl.label}}</span><b>{{num(sl.value)}} ({{sl.pctLabel}}%)</b></div>
            <p v-if="!sec.data.slices.length" class="quiet" style="font-size:12px">No {{sec.valueLabel.toLowerCase()}} data</p>
          </div>
        </div>
        <div class="perf-table-scroll"><table><thead><tr><th>Period</th><th>{{panels[sec.key].groupBy==='dealer'?'Dealer':'Region'}}</th><th>{{sec.valueLabel}}</th><th>Share</th></tr></thead>
          <tbody>
            <tr v-for="(r,i) in sec.data.table.slice(0,25)" :key="r.bucket+'-'+r.scope+'-'+i">
              <td>{{r.label}}</td><td><strong>{{r.scope}}</strong></td><td><b>{{num(r.value)}}</b></td><td>{{r.share}}%</td>
            </tr>
            <tr v-if="!sec.data.table.length"><td colspan="4" class="quiet" style="text-align:center;padding:18px">No matching records</td></tr>
          </tbody>
        </table></div>
      </section>
    </div>

    <section class="panel activity-panel">
      <div class="panel-head overview-summary-head">
        <div><h2>Recent activity · SA Visit &amp; Miss Summary</h2><p>Date from {{formatDMY(s.fromDate)}} to date {{formatDMY(s.toDate)}}</p></div>
        <div class="overview-tools">
          <select v-model="regFilter" class="control small-control" aria-label="Filter summary by region"><option value="">All regions</option><option v-for="r in summaryRegions" :key="r">{{r}}</option></select>
          <select v-model="dealerFilter" class="control small-control" aria-label="Filter summary by dealer"><option value="">All dealers</option><option v-for="d in summaryDealers" :key="d">{{d}}</option></select>
          <label class="search-box small-search"><Search :size="14"/><input v-model="saSearch" aria-label="Search SA summary" placeholder="Search SA name, ID..."></label>
          <button type="button" class="btn small-btn" @click="exportOverviewCsv"><Download :size="14"/>Export CSV</button>
          <a class="outline-link" href="#activity">Event stream →</a>
        </div>
      </div>
      <div class="table-scroll"><table><thead><tr><th>SA Name</th><th>Telegram ID</th><th>Region / Dealer</th><th>Controller</th><th>Total Visit</th><th>Total Miss</th></tr></thead><tbody>
        <tr v-for="r in visibleSummary" :key="r.telegram_id+'-'+r.group">
          <td><div class="person"><ProfileAvatar :telegram-id="r.telegram_id" :name="r.name"/><div><strong>{{r.name}}</strong><small>{{r.group}}</small></div></div></td>
          <td>{{r.telegram_id}}</td><td>{{r.region}} / {{r.dealer||'—'}}</td><td>{{r.controller||'—'}}</td><td><strong>{{num(r.total_visit)}}</strong></td>
          <td><b :class="r.total_miss?'miss-count':''">{{num(r.total_miss)}}</b></td>
        </tr></tbody></table></div>
      <div v-if="!visibleSummary.length" class="empty"><SearchX/><strong>No activity in this date range</strong><p>Change From Date / To Date or clear filters to view SA records.</p></div>
      <div v-if="filteredSummary.length" class="pagination"><span>Page {{summaryPage}} of {{summaryPages}} · {{num(filteredSummary.length)}} SAs</span>
        <button class="icon" aria-label="Previous summary page" :disabled="summaryPage<=1" @click="summaryPage--"><ChevronLeft/></button>
        <button class="icon" aria-label="Next summary page" :disabled="summaryPage>=summaryPages" @click="summaryPage++"><ChevronRight/></button></div>
    </section>
  </template>
  <section v-else class="panel activity-panel">
    <div class="panel-head"><div><h2>Field activity</h2><p>Events from {{formatDMY(s.fromDate)}} to {{formatDMY(s.toDate)}}</p></div>
      <select class="control small-control" aria-label="Filter activity type" :value="s.kind" @change="filter($event.target.value)"><option value="all">All events</option><option v-for="(label,k) in eventNames" :key="k" :value="k">{{label}}</option></select></div>
    <div class="table-scroll"><table><thead><tr><th>Team member</th><th>Event</th><th>Group</th><th>Details</th><th>Time</th></tr></thead><tbody>
      <tr v-for="r in s.events" :key="r.id">
        <td><div class="person"><ProfileAvatar :telegram-id="r.telegram_id" :name="r.name"/><strong>{{r.name}}</strong></div></td>
        <td><span class="event-kind"><component :is="icons[r.kind]" :size="14"/>{{eventNames[r.kind]}}</span></td>
        <td>{{r.group}}</td><td><span class="reason">{{r.detail}}</span></td>
        <td class="time-cell">{{dateLabel(r.at,{month:'short',day:'numeric'})}} · {{r.at.slice(11,16)}}</td>
      </tr></tbody></table></div>
    <div v-if="!s.events.length" class="empty"><SearchX/><strong>No activity in this period</strong><p>Telegram events will appear here as they arrive.</p></div>
    <div v-if="s.total" class="pagination"><span>{{(s.page-1)*10+1}}–{{Math.min(s.page*10,s.total)}} of {{num(s.total)}} events</span><button class="icon" aria-label="Previous page" :disabled="s.page===1||s.loading" @click="turn(s.page-1)"><ChevronLeft/></button><button class="icon" aria-label="Next page" :disabled="s.page*10>=s.total||s.loading" @click="turn(s.page+1)"><ChevronRight/></button></div>
  </section>
</template>
