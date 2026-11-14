<script setup>
import { computed } from 'vue'
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BellRing, ChevronLeft, ChevronRight, ClipboardCheck,
  FileText, Layers3, MapPin, Minus, SearchX, Users, Waypoints } from 'lucide-vue-next'
import { dateLabel, eventNames, initials, num, useWorkspace } from './workspace'
import TrendChart from './TrendChart.vue'
import ProfileAvatar from './ProfileAvatar.vue'
defineProps({activityOnly:Boolean})
const emit=defineEmits(['navigate'])
const {s,refresh}=useWorkspace()
const totals=computed(()=>Object.fromEntries(['reports','pins','alerts'].map(k=>[k,s.metrics.series.reduce((a,v)=>a+v[k],0)])))
const regions=computed(()=>{
  const counts=new Map()
  for(const r of s.data.roster) if(r.enabled) counts.set(r.region,(counts.get(r.region)||0)+1)
  return [...counts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))
})
const active=computed(()=>regions.value.reduce((n,r)=>n+r[1],0))
const cards=computed(()=>[
  {label:'Roster members',value:active.value,icon:Users,color:'teal',page:'roster'},
  {label:'Sale reports',key:'reports',value:totals.value.reports,icon:ClipboardCheck,color:'blue'},
  {label:'Location pins',key:'pins',value:totals.value.pins,icon:MapPin,color:'violet'},
  {label:'Missing alerts',key:'alerts',value:totals.value.alerts,icon:BellRing,color:'amber'},
])
function change(k){const prev=s.metrics.previous[k];return prev?Math.round((totals.value[k]-prev)*100/prev):null}
const icons={reports:FileText,pins:MapPin,alerts:BellRing}
const colors=['#197c70','#44988c','#74afa0','#a7c9b5','#d8ad7c','#b3bbd1','#c1cfd0','#d4dcd9']
const donut=computed(()=>{
  let sum=0
  return regions.value.map(([label,value],i)=>{
    const from=sum;const part=value/active.value*100;sum+=part
    return {label,value,from,part,color:colors[i%colors.length]}
  })
})
async function filter(k){s.kind=k;s.page=1;await refresh()}
async function turn(n){s.page=n;await refresh()}
function openMetric(card){
  if(card.page){emit('navigate',card.page);return}
  emit('navigate','activity/'+card.key)
}
</script>
<template>
  <template v-if="!activityOnly">
    <div class="metrics-grid"><button v-for="c in cards" :key="c.label" class="metric" @click="openMetric(c)">
      <span class="metric-top"><span class="icon-tile" :class="c.color"><component :is="c.icon" :size="19"/></span><ArrowUpRight class="metric-arrow" :size="15"/></span>
      <span class="metric-label">{{c.label}}</span><strong class="metric-value">{{num(c.value)}}</strong>
      <span v-if="!c.key" class="metric-note"><i class="status-dot"></i>Across {{regions.length}} regions</span>
      <span v-else class="metric-note"><template v-if="change(c.key)!==null"><b class="delta" :class="{negative:(c.key==='alerts'?change(c.key)>0:change(c.key)<0)}">
        <component :is="change(c.key)>0?ArrowUpRight:change(c.key)<0?ArrowDownRight:Minus" :size="11"/>{{Math.abs(change(c.key))}}%</b>vs. previous period</template><template v-else>Previous period · {{num(s.metrics.previous[c.key])}}</template></span>
    </button></div>
    <div class="analytics-grid">
      <section class="panel trend"><div class="panel-head"><div><h2>Reporting activity</h2><p>Daily reports and location pins</p></div><span class="panel-glyph"><Activity :size="18"/></span></div>
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
  </template>
  <section class="panel activity-panel">
    <div class="panel-head"><div><h2>{{activityOnly?'Field activity':'Recent activity'}}</h2><p>{{activityOnly?'Events in the selected reporting period':'Latest events from your Telegram groups'}}</p></div>
      <a v-if="!activityOnly" class="outline-link" href="#activity">View all <ArrowRight :size="14"/></a>
      <select v-else class="control small-control" aria-label="Filter activity type" :value="s.kind" @change="filter($event.target.value)"><option value="all">All events</option><option v-for="(label,k) in eventNames" :key="k" :value="k">{{label}}</option></select></div>
    <div class="table-scroll"><table><thead><tr><th>Team member</th><th>Event</th><th>Group</th><th>{{activityOnly?'Details':'Status'}}</th><th>Time</th></tr></thead><tbody>
      <tr v-for="(r,i) in (activityOnly?s.events:s.events.slice(0,5))" :key="r.id">
        <td><div class="person"><ProfileAvatar :telegram-id="r.telegram_id" :name="r.name"/><strong>{{r.name}}</strong></div></td>
        <td><span class="event-kind"><component :is="icons[r.kind]" :size="14"/>{{eventNames[r.kind]}}</span></td>
        <td>{{r.group}}</td><td><span v-if="activityOnly" class="reason">{{r.detail}}</span><span v-else class="badge" :class="r.kind==='alerts'&&!r.reason?'amber':'teal'"><i></i>{{r.kind==='alerts'?(r.reason?'Reason received':'Awaiting reason'):'Received'}}</span></td>
        <td class="time-cell">{{dateLabel(r.at,{month:'short',day:'numeric'})}} · {{r.at.slice(11,16)}}</td>
      </tr></tbody></table></div>
    <div v-if="!s.events.length" class="empty"><SearchX/><strong>No activity in this period</strong><p>Telegram events will appear here as they arrive.</p></div>
    <div v-if="activityOnly && s.total" class="pagination"><span>{{(s.page-1)*10+1}}–{{Math.min(s.page*10,s.total)}} of {{num(s.total)}} events</span><button class="icon" aria-label="Previous page" :disabled="s.page===1||s.loading" @click="turn(s.page-1)"><ChevronLeft/></button><button class="icon" aria-label="Next page" :disabled="s.page*10>=s.total||s.loading" @click="turn(s.page+1)"><ChevronRight/></button></div>
  </section>
</template>
