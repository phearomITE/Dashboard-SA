<script setup>
import {computed,onMounted,reactive,ref,watch} from 'vue'
import {Check,Download,Pencil,Search,Trash2,X} from 'lucide-vue-next'
import {useWorkspace} from './workspace'
import ProfileAvatar from './ProfileAvatar.vue'
import './missing-report.css'

const {s,request,refresh,notify}=useWorkspace()
const today=s.data.now.slice(0,10)
const from=ref(today),to=ref(today),controller=ref(''),search=ref(''),rows=ref([]),busy=ref(false),error=ref('')
const modal=ref(null),modalBusy=ref(false),modalError=ref(''),deleteMode=ref('all')
const editForm=reactive({name:'',region:'R1',dealer:'',reason:''})
let serial=0
const controllers=computed(()=>[...new Set([...(s.data?.roster||[]).map(r=>r.controller),...rows.value.map(r=>r.controller)].filter(v=>v&&v!=='Unassigned'))].sort())
const totalSa=computed(()=>(s.data?.roster||[]).filter(r=>(!controller.value||r.controller===controller.value)&&
  (!search.value||[r.name,r.telegram_id,r.group,r.dealer,r.region].some(v=>String(v||'').toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))).length)
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
function openEdit(row){
  modalError.value=''
  editForm.name=row.name||''
  editForm.region=row.region||'R1'
  editForm.dealer=row.dealer||''
  editForm.reason=row.reasons.join(' | ')
  modal.value={type:'edit',row}
}
function openDelete(row){
  modalError.value=''
  deleteMode.value='all'
  modal.value={type:'delete',row}
}
function closeModal(){if(!modalBusy.value)modal.value=null}
async function submitModal(){
  if(!modal.value||modalBusy.value)return
  const {type,row}=modal.value
  modalBusy.value=true;modalError.value=''
  try{
    if(type==='edit'){
      const res=await request('missing-report/'+encodeURIComponent(row.telegram_id),'PUT',{
        from_date:from.value,to_date:to.value,name:editForm.name,region:editForm.region,dealer:editForm.dealer,reason:editForm.reason
      })
      await Promise.all([load(),refresh()])
      notify(res?.message||'Miss report updated.')
    }else{
      const res=await request('missing-report/'+encodeURIComponent(row.telegram_id)+'?'+new URLSearchParams({
        from_date:from.value,to_date:to.value,mode:deleteMode.value
      }),'DELETE')
      await Promise.all([load(),refresh()])
      notify(res?.message||'Miss report deleted.')
    }
    modal.value=null
  }catch(e){modalError.value=e.message}finally{modalBusy.value=false}
}
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
  <div class="miss-summary"><div><small>Total SA</small><strong>{{totalSa}}</strong></div><div><small>SA Miss Report</small><strong>{{filtered.length}}</strong></div><div><small>Total Visit · pins</small><strong>{{totals.visit}}</strong></div><div><small>Total Miss · alerts</small><strong>{{totals.miss}}</strong></div><div><small>Written reasons</small><strong>{{totals.reasons}}</strong></div></div>
  <p v-if="!busy&&!totals.miss" class="miss-note">No missing-report alerts are stored for this date range. Visits are shown from recorded location pins.</p>
  <div class="miss-table-wrap"><table><thead><tr><th>SA Name</th><th>Telegram ID</th><th>Region / Dealer</th><th>Controller</th><th>Total Visit</th><th>Miss Report</th><th>Total Miss</th><th>Summary Reason</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody><tr v-for="r in filtered" :key="r.telegram_id+'-'+r.group"><td><div class="person"><ProfileAvatar :telegram-id="r.telegram_id" :name="r.name"/><span><strong>{{r.name}}</strong><small>{{r.group}}</small></span></div></td><td>{{r.telegram_id}}</td><td>{{r.region}} / {{r.dealer||'—'}}</td><td>{{r.controller||'—'}}</td><td>{{r.total_visit}}</td><td>{{r.total_miss?(r.reasons.length?'Reason received':'Awaiting reason'):'No alert'}}</td><td><b :class="r.total_miss?'miss-count':''">{{r.total_miss}}</b></td><td class="miss-reasons"><span v-if="r.reasons.length" v-for="reason in r.reasons" :key="reason">{{reason}}</span><span v-if="!r.reasons.length">{{r.total_miss?'Awaiting reason':'—'}}</span></td><td><div class="row-actions"><button class="icon action-icon" :aria-label="'Edit '+r.name" title="Edit miss report" @click="openEdit(r)"><Pencil :size="15"/></button><button class="icon action-icon danger-action" :aria-label="'Delete '+r.name" title="Delete miss report" @click="openDelete(r)"><Trash2 :size="15"/></button></div></td></tr>
      <tr v-if="!filtered.length"><td colspan="9" class="miss-empty">{{busy?'Loading…':'No activity for these filters.'}}</td></tr></tbody></table></div>
</section>
<Teleport v-if="modal" to="body"><div class="dialog-overlay" @click.self="closeModal"><section class="dialog" role="dialog" aria-modal="true" aria-labelledby="miss-dialog-title">
  <div class="dialog-head"><div><span class="dialog-context">{{modal.row.name}} · {{modal.row.telegram_id}}</span><h2 id="miss-dialog-title">{{modal.type==='edit'?'Edit miss report':'Delete miss report?'}}</h2></div><button class="icon" aria-label="Close dialog" :disabled="modalBusy" @click="closeModal"><X :size="18"/></button></div>
  <form @submit.prevent="submitModal">
    <template v-if="modal.type==='edit'">
      <label>SA name<input v-model.trim="editForm.name" maxlength="255" required></label>
      <label>Region<select v-model="editForm.region"><option v-for="n in 8" :key="n">{{'R'+n}}</option></select></label>
      <label>Dealer<input v-model.trim="editForm.dealer" maxlength="50" required></label>
      <label>Summary reason <small>(optional)</small><input v-model.trim="editForm.reason" maxlength="2000" placeholder="Enter written reason for selected date range"></label>
    </template>
    <template v-else>
      <div class="dialog-copy">Remove missing-report records for <strong>{{modal.row.name}}</strong> between {{from}} and {{to}}?</div>
      <label style="margin-top:12px">Delete action<select v-model="deleteMode"><option value="all">Remove row from Miss Report (alerts &amp; visits)</option><option value="alerts">Clear missing alerts only (keep visits)</option></select></label>
    </template>
    <div class="dialog-actions"><button type="button" class="btn" :disabled="modalBusy" @click="closeModal">Cancel</button><button class="primary" :disabled="modalBusy"><component :is="modal.type==='edit'?Check:Trash2" :size="15"/>{{modalBusy?'Working…':modal.type==='edit'?'Save changes':'Delete'}}</button></div>
  </form>
  <p v-if="modalError" class="error-text" role="alert">{{modalError}}</p>
</section></div></Teleport>
</template>
