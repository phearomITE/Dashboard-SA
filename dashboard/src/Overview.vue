<script setup>
import { computed, ref, watch } from 'vue'
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BellRing, CheckCircle2, ChevronLeft, ChevronRight,
  ClipboardCheck, Download, FileText, Layers3, MapPin, MessageSquareWarning, Minus, Search, SearchX, Users, Waypoints } from 'lucide-vue-next'
import { dateLabel, eventNames, formatDMY, initials, num, useWorkspace } from './workspace'
import TrendChart from './TrendChart.vue'
import ProfileAvatar from './ProfileAvatar.vue'
defineProps({activityOnly:Boolean})
const emit=defineEmits(['navigate'])
const {s,refresh}=useWorkspace()
const totals=computed(()=>Object.fromEntries(['reports','pins','alerts'].map(k=>[k,(s.metrics?.series||[]).reduce((a,v)=>a+v[k],0)])))
const regions=computed(()=>{
  const counts=new Map()
  for(const r of (s.data?.roster||[])) if(r.enabled) counts.set(r.region,(counts.get(r.region)||0)+1)
  return [...counts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))
})
const active=computed(()=>regions.value.reduce((n,r)=>n+r[1],0))
const complianceRate=computed(()=>{
  const denom=totals.value.pins+totals.value.alerts
  return denom?Math.round((totals.value.pins/denom)*100):100
})
const writtenTotal=computed(()=>s.metrics?.written_alerts??(s.overviewRows||[]).reduce((a,r)=>a+(r.written_count??r.reasons?.length??0),0))
const notWrittenTotal=computed(()=>s.metrics?.unwritten_alerts??(s.overviewRows||[]).reduce((a,r)=>a+(r.unwritten_count??Math.max(0,(r.total_miss||0)-(r.reasons?.length||0))),0))
const reasonRate=computed(()=>{
  const denom=writtenTotal.value+notWrittenTotal.value
  return denom?Math.round((writtenTotal.value/denom)*100):100
})
const cards=computed(()=>[
  {label:'Roster members',value:active.value,icon:Users,color:'teal',page:'roster'},
  {label:'Sale reports',key:'reports',value:totals.value.reports,icon:ClipboardCheck,color:'blue'},
  {label:'Location pins (Visit)',key:'pins',value:totals.value.pins,icon:MapPin,color:'violet'},
  {label:'Missing alerts',key:'alerts',value:totals.value.alerts,icon:BellRing,color:'amber'},
  {label:'Compliance rate',kind:'compliance',value:complianceRate.value+'%',icon:CheckCircle2,color:'teal'},
  {label:'Written vs Not Written',kind:'reasons',value:num(writtenTotal.value)+' / '+num(notWrittenTotal.value),icon:MessageSquareWarning,color:'amber',page:'missing-report'},
])
function change(k){const prev=s.metrics?.previous?.[k];return prev?Math.round((totals.value[k]-prev)*100/prev):null}
const icons={reports:FileText,pins:MapPin,alerts:BellRing}
const colors=['#197c70','#44988c','#74afa0','#a7c9b5','#d8ad7c','#b3bbd1','#c1cfd0','#d4dcd9']
const donut=computed(()=>{
  let sum=0
  const denom=Math.max(1,active.value)
  return regions.value.map(([label,value],i)=>{
    const from=sum;const part=value/denom*100;sum+=part
    return {label,value,from,part,color:colors[i%colors.length]}
  })
})
const regionalAnalysis=computed(()=>{
  const saByReg=new Map(regions.value)
  return (s.metrics?.regional||[]).map(r=>{
    const total=r.pins+r.alerts
    const compliance=total?Math.round((r.pins/total)*100):100
    const written=r.written_reasons??r.reasons??0
    const notWritten=r.unwritten_reasons??Math.max(0,r.alerts-written)
    return {...r,sa:saByReg.get(r.region)||0,written,notWritten,compliance}
  })
})
const controllerAnalysis=computed(()=>{
  const byCtrl=new Map()
  const saCounts=new Map()
  for(const m of (s.data?.roster||[])){
    if(!m.enabled||!m.controller)continue
    saCounts.set(m.controller,(saCounts.get(m.controller)||0)+1)
  }
  for(const r of (s.overviewRows||[])){
    const key=r.controller||'Unassigned'
    if(!byCtrl.has(key))byCtrl.set(key,{controller:key,sas:saCounts.get(key)||0,visit:0,miss:0,awaiting:0})
    const item=byCtrl.get(key)
    item.visit+=r.total_visit||0
    item.miss+=r.total_miss||0
    item.awaiting+=r.unwritten_count??Math.max(0,(r.total_miss||0)-(r.reasons?.length||0))
  }
  return [...byCtrl.values()].map(c=>{
    const denom=c.visit+c.miss
    return {...c,compliance:denom?Math.round((c.visit/denom)*100):100}
  }).sort((a,b)=>b.miss-a.miss||b.visit-a.visit)
})

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
async function filter(k){s.kind=k;s.page=1;await refresh()}
async function turn(n){s.page=n;await refresh()}
function openMetric(card){
  if(card.page){emit('navigate',card.page);return}
  if(card.key)emit('navigate','activity/'+card.key)
}
</script>
<template>
  <template v-if="!activityOnly">
    <div class="metrics-grid"><button v-for="c in cards" :key="c.label" class="metric" @click="openMetric(c)">
      <span class="metric-top"><span class="icon-tile" :class="c.color"><component :is="c.icon" :size="19"/></span><ArrowUpRight v-if="c.page||c.key" class="metric-arrow" :size="15"/></span>
      <span class="metric-label">{{c.label}}</span><strong class="metric-value">{{typeof c.value==='number'?num(c.value):c.value}}</strong>
      <span v-if="c.kind==='compliance'" class="metric-note"><i class="status-dot"></i>{{num(s.metrics?.visitors||0)}} active SAs checking in</span>
      <span v-else-if="c.kind==='reasons'" class="metric-note">{{reasonRate}}% reason resolution · click for Miss Report</span>
      <span v-else-if="!c.key" class="metric-note"><i class="status-dot"></i>Across {{regions.length}} regions</span>
      <span v-else class="metric-note"><template v-if="change(c.key)!==null"><b class="delta" :class="{negative:(c.key==='alerts'?change(c.key)>0:change(c.key)<0)}">
        <component :is="change(c.key)>0?ArrowUpRight:change(c.key)<0?ArrowDownRight:Minus" :size="11"/>{{Math.abs(change(c.key))}}%</b>vs. previous period</template><template v-else>Previous period · {{num(s.metrics?.previous?.[c.key])}}</template></span>
    </button></div>
    <div class="analytics-grid">
      <section class="panel trend"><div class="panel-head"><div><h2>Reporting activity</h2><p>Daily reports and location pins ({{formatDMY(s.fromDate)}} – {{formatDMY(s.toDate)}})</p></div><span class="panel-glyph"><Activity :size="18"/></span></div>
        <div class="chart-legend"><span><i></i>Sale reports</span><span><i class="pins-line"></i>Location pins</span></div>
        <TrendChart :series="s.metrics.series"/></section>
      <section class="panel coverage"><div class="panel-head"><div><h2>Regional coverage</h2><p>Enabled members by region</p></div><Waypoints :size="18" class="quiet"/></div>
        <div class="coverage-body"><div class="ring-wrap"><svg viewBox="0 0 160 160" role="img" aria-label="Enabled roster members by region">
          <circle cx="80" cy="80" r="64" fill="none" stroke="#edf3f1" stroke-width="19"/>
          <circle v-for="a in donut" :key="a.label" cx="80" cy="80" r="64" fill="none" :stroke="a.color" stroke-width="19" pathLength="100" :stroke-dasharray="Math.max(.05,a.part-.85)+' '+(100-Math.max(.05,a.part-.85))" :stroke-dashoffset="-a.from" transform="rotate(-90 80 80)"><title>{{a.label}} · {{a.value}} members</title></circle></svg>
          <div><strong>{{num(active)}}</strong><small>members</small></div></div>
          <div class="region-legend"><div v-for="a in donut" :key="a.label"><i :style="{background:a.color}"></i><span>{{a.label}}</span><b>{{a.value}}</b></div><p v-if="!donut.length">No enabled members</p></div>
        </div><a class="panel-footer" href="#roster">View SA roster <ArrowRight :size="14"/></a>
      </section>
    </div>
    <div class="analysis-grid">
      <section class="panel"><div class="panel-head"><div><h2>Regional performance analysis</h2><p>Visits, alerts, reason completion &amp; compliance by region</p></div><span class="panel-glyph"><Layers3 :size="17"/></span></div>
        <div class="table-scroll" style="margin-top:12px"><table><thead><tr><th>Region</th><th>Active SA</th><th>Total Visit</th><th>Reports</th><th>Total Miss</th><th>Written</th><th>Not Written</th><th>Compliance</th></tr></thead>
          <tbody><tr v-for="r in regionalAnalysis" :key="r.region"><td><strong>{{r.region}}</strong></td><td>{{num(r.sa)}}</td><td>{{num(r.pins)}}</td><td>{{num(r.reports)}}</td><td><b :class="r.alerts?'miss-count':''">{{num(r.alerts)}}</b></td><td>{{num(r.written)}}</td><td>{{num(r.notWritten)}}</td>
            <td><div class="compliance-cell"><span class="compliance-track"><i :style="{width:r.compliance+'%'}"></i></span><b>{{r.compliance}}%</b></div></td></tr>
            <tr v-if="!regionalAnalysis.length"><td colspan="8" class="empty">No regional metrics in selected dates.</td></tr></tbody></table></div></section>
      <section class="panel"><div class="panel-head"><div><h2>Controller team analysis</h2><p>Controller performance across selected date range</p></div><a class="outline-link" href="#missing-report">Miss Report <ArrowRight :size="14"/></a></div>
        <div class="table-scroll" style="margin-top:12px"><table><thead><tr><th>Controller</th><th>SAs</th><th>Total Visit</th><th>Total Miss</th><th>Awaiting</th><th>Compliance</th></tr></thead>
          <tbody><tr v-for="c in controllerAnalysis" :key="c.controller"><td><strong>{{c.controller}}</strong></td><td>{{num(c.sas)}}</td><td>{{num(c.visit)}}</td><td><b :class="c.miss?'miss-count':''">{{num(c.miss)}}</b></td><td>{{num(c.awaiting)}}</td>
            <td><div class="compliance-cell"><span class="compliance-track"><i :style="{width:c.compliance+'%'}"></i></span><b>{{c.compliance}}%</b></div></td></tr>
            <tr v-if="!controllerAnalysis.length"><td colspan="6" class="empty">No controller metrics in selected dates.</td></tr></tbody></table></div></section>
    </div>
    <section class="panel activity-panel">
      <div class="panel-head overview-summary-head">
        <div><h2>Recent activity · SA Visit &amp; Miss Summary</h2><p>Date from {{formatDMY(s.fromDate)}} to date {{formatDMY(s.toDate)}}</p></div>
        <div class="overview-tools">
          <select v-model="regFilter" class="control small-control" aria-label="Filter summary by region"><option value="">All regions</option><option v-for="r in summaryRegions" :key="r">{{r}}</option></select>
          <select v-model="dealerFilter" class="control small-control" aria-label="Filter summary by dealer"><option value="">All dealers</option><option v-for="d in summaryDealers" :key="d">{{d}}</option></select>
          <label class="search-box small-search"><Search :size="14"/><input v-model="saSearch" aria-label="Search SA summary" placeholder="Search SA name, ID..."></label>
          <button type="button" class="btn small-btn" @click="exportOverviewCsv"><Download :size="14"/>Export CSV</button>
          <a class="outline-link" href="#activity">Event stream <ArrowRight :size="14"/></a>
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
