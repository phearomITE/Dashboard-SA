<script setup>
import {computed,onMounted,ref,watch} from 'vue'
import {Download,Search} from 'lucide-vue-next'
import {useWorkspace} from './workspace'
import ProfileAvatar from './ProfileAvatar.vue'
import './missing-report.css'

const {s,request}=useWorkspace()
const today=s.data.now.slice(0,10)
const from=ref(today),to=ref(today),controller=ref(''),search=ref(''),rows=ref([]),busy=ref(false),error=ref('')
let serial=0
const controllers=computed(()=>[...new Set(rows.value.map(r=>r.controller).filter(Boolean))].sort())
const filtered=computed(()=>rows.value.filter(r=>(!controller.value||r.controller===controller.value)&&
  (!search.value||[r.name,r.telegram_id,r.group,r.dealer,r.region].some(v=>String(v||'').toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))))
const totals=computed(()=>filtered.value.reduce((a,r)=>({visit:a.visit+r.total_visit,
  miss:a.miss+r.total_miss,reasons:a.reasons+r.reasons.length}),{visit:0,miss:0,reasons:0}))
async function load(){
  if(!from.value||!to.value||from.value>to.value){error.value='Choose a valid From and To date.';return}
  const run=++serial;busy.value=true;error.value=''
  try{const result=await request('missing-report?'+new URLSearchParams({from_date:from.value,to_date:to.value}));if(run===serial)rows.value=result.rows}
  catch(e){if(run===serial)error.value=e.message}finally{if(run===serial)busy.value=false}
}
watch([from,to],load);onMounted(load)
function exportCsv(){
  const header=['Telegram ID','SA Name','Region','Dealer','Group','Controller','Total Visit','Miss Report','Total Miss','Reason Count','Summary Reason']
  const lines=[header,...filtered.value.map(r=>[r.telegram_id,r.name,r.region,r.dealer,r.group,r.controller,r.total_visit,r.total_miss?(r.reasons.length?'Reason received':'Awaiting reason'):'No alert',r.total_miss,r.reasons.length,r.reasons.join(' | ')])]
  const csv='\uFEFF'+lines.map(line=>line.map(value=>'"'+String(value??'').replaceAll('"','""')+'"').join(',')).join('\r\n')
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a')
  a.href=url;a.download=`miss-report-${from.value}-to-${to.value}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
</script>
<template><section class="miss-report panel">
  <div class="miss-filters">
    <label>From Date<input type="date" v-model="from" :max="today"></label>
    <label>To Date<input type="date" v-model="to" :max="today"></label>
    <label>Controller<select v-model="controller"><option value="">All controllers</option><option v-for="v in controllers" :key="v">{{v}}</option></select></label>
    <label class="miss-search">Search SA<div><Search :size="15"/><input type="search" v-model="search" placeholder="Name, Telegram ID, dealer..."></div></label>
    <button class="btn" @click="exportCsv"><Download :size="15"/>Export CSV</button>
  </div>
  <p v-if="error" class="error-banner" role="alert">{{error}}</p>
  <div class="miss-summary"><div><small>SA with activity</small><strong>{{filtered.length}}</strong></div><div><small>Total Visit · pins</small><strong>{{totals.visit}}</strong></div><div><small>Total Miss · alerts</small><strong>{{totals.miss}}</strong></div><div><small>Written reasons</small><strong>{{totals.reasons}}</strong></div></div>
  <p v-if="!busy&&!totals.miss" class="miss-note">No missing-report alerts are stored for this date range. Visits are shown from recorded location pins.</p>
  <div class="miss-table-wrap"><table><thead><tr><th>SA Name</th><th>Telegram ID</th><th>Region / Dealer</th><th>Controller</th><th>Total Visit</th><th>Miss Report</th><th>Total Miss</th><th>Summary Reason</th></tr></thead>
    <tbody><tr v-for="r in filtered" :key="r.telegram_id+'-'+r.group"><td><div class="person"><ProfileAvatar :telegram-id="r.telegram_id" :name="r.name"/><span><strong>{{r.name}}</strong><small>{{r.group}}</small></span></div></td><td>{{r.telegram_id}}</td><td>{{r.region}} / {{r.dealer||'—'}}</td><td>{{r.controller||'—'}}</td><td>{{r.total_visit}}</td><td>{{r.total_miss?(r.reasons.length?'Reason received':'Awaiting reason'):'No alert'}}</td><td><b :class="r.total_miss?'miss-count':''">{{r.total_miss}}</b></td><td class="miss-reasons"><span v-if="r.reasons.length" v-for="reason in r.reasons" :key="reason">{{reason}}</span><span v-if="!r.reasons.length">{{r.total_miss?'Awaiting reason':'—'}}</span></td></tr>
      <tr v-if="!filtered.length"><td colspan="8" class="miss-empty">{{busy?'Loading…':'No activity for these filters.'}}</td></tr></tbody></table></div>
</section></template>
