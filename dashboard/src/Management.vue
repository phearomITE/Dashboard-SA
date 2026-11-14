<script setup>
import {computed, ref, watch} from 'vue'
import {Bell, Boxes, CalendarClock, ChevronLeft, ChevronRight, CircleHelp, Clock3, Database, Layers3, MapPin,
  MoreHorizontal, Pencil, Radio, Search, ShieldCheck, SlidersHorizontal, Trash2, Users} from 'lucide-vue-next'
import {initials,num,useWorkspace} from './workspace'
import ProfileAvatar from './ProfileAvatar.vue'
const props=defineProps({page:String})
const {s,save}=useWorkspace()
const query=ref(''), status=ref('All statuses'), region=ref('All regions'), controller=ref('All controllers'), currentPage=ref(1), shift=ref(0)
const controllerGroupId=ref(s.data.controller_alert_group_id||'-1004464440125'), controllerAlertsEnabled=ref(s.data.controller_alerts_enabled),settingsBusy=ref(false),settingsError=ref('')
async function saveControllerAlerts(){
  if(!/^-\d+$/.test(controllerGroupId.value)){settingsError.value='Enter a negative Telegram group ID.';return}
  settingsBusy.value=true;settingsError.value=''
  try{await save('monitoring/controller-alerts','PUT',{enabled:controllerAlertsEnabled.value,group_id:Number(controllerGroupId.value)},'Controller alert settings saved.')}
  catch(e){settingsError.value=e.message}finally{settingsBusy.value=false}
}
const lists={roster:'roster',groups:'groups',products:'products',controllers:'controllers'}
const icons={roster:Users,groups:Layers3,products:Boxes,controllers:ShieldCheck}
const labels={roster:'members',groups:'groups',products:'products',controllers:'assignments'}
const columns={roster:['Telegram ID','SA Name','Username','Region','Dealer','Position','Working Time','Group','Controller','Status'],groups:['Telegram group','Region','Members','Monitoring'],
  products:['Product','SKU','Category','Status'],controllers:['Controller','Telegram ID','Scope','Assignment']}
const rows=computed(()=>s.data[lists[props.page]]||[])
const filtered=computed(()=>rows.value.filter(row=>{
  const match=Object.values(row).some(v=>String(v??'').toLocaleLowerCase().includes(query.value.toLocaleLowerCase().trim()))
  return match&&(status.value==='All statuses'||row.enabled===(status.value==='Enabled'))&&(region.value==='All regions'||row.region===region.value)
    &&(props.page!=='roster'||controller.value==='All controllers'||row.controller===controller.value)
}))
const pages=computed(()=>Math.max(1,Math.ceil(filtered.value.length/12)))
const visible=computed(()=>filtered.value.slice((currentPage.value-1)*12,currentPage.value*12))
watch([query,status,region,controller],()=>{currentPage.value=1})
watch(pages,v=>{currentPage.value=Math.min(currentPage.value,v)})
function manage(row){
  const type={roster:'manageMember',products:'product',groups:'manageGroup',controllers:'manageController'}[props.page]
  s.form={type,row}
}
function edit(row){s.form={type:'editMember',row}}
function remove(row){s.form={type:'deleteMember',row}}
function editController(row){
  const scope=(row.region||'')+'-'+(row.dealer||'')
  const existing=s.data.controllers.find(c=>c.scope===scope)
  s.form={type:'memberController',row:{...row,controller_name:existing?.name||'',controller_telegram_id:existing?.telegram_id||'',controller_username:existing?.username||''}}
}
</script>
<template>
  <section v-if="lists[page]" class="panel records">
    <div class="record-tools"><label class="search-box"><Search :size="16"/><input v-model="query" :aria-label="'Search '+labels[page]" :placeholder="'Search '+labels[page]+'…'"></label>
      <select v-if="['roster','groups'].includes(page)" v-model="region" class="control" aria-label="Region"><option>All regions</option><option v-for="r in [...new Set(rows.map(x=>x.region).filter(Boolean))].sort()" :key="r">{{r}}</option></select>
      <select v-if="page==='roster'" v-model="controller" class="control" aria-label="Controller filter"><option>All controllers</option><option v-for="name in [...new Set(rows.map(x=>x.controller).filter(Boolean))].sort()" :key="name">{{name}}</option></select>
      <select v-if="page!=='controllers'" v-model="status" class="control" aria-label="Status filter"><option>All statuses</option><option>Enabled</option><option>Disabled</option></select>
      <span class="record-count">{{num(filtered.length)}} {{labels[page]}}</span></div>
    <div class="table-scroll"><table :class="{'roster-table':page==='roster'}"><thead><tr><th v-for="c in columns[page]" :key="c">{{c}}</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>
      <tr v-for="r in visible" :key="r.id||r.scope+'-'+r.telegram_id">
        <td v-if="page==='roster'" class="roster-id">{{r.telegram_id||'—'}}</td>
        <td><div class="person"><ProfileAvatar v-if="['roster','controllers'].includes(page)" :telegram-id="r.telegram_id" :name="r.name"/>
          <span v-else class="avatar" :class="page==='products'?'tone-2':'tone-0'"><component :is="icons[page]" :size="16"/></span>
          <div><strong>{{r.name}}</strong><small v-if="page==='groups'">{{r.id}}</small></div></div></td>
        <template v-if="page==='roster'"><td>{{r.username?('@'+r.username.replace(/^@/,'')):'—'}}</td><td>{{r.region||'—'}}</td><td>{{r.dealer||'—'}}</td><td>{{r.position}}</td><td>{{r.shift}}</td><td>{{r.group}}</td><td class="controller-cell" :title="r.controller"><button class="controller-edit" :aria-label="'Edit controller for '+r.name" @click="editController(r)">{{r.controller||'Assign controller'}} <Pencil :size="12"/></button></td><td><span class="badge" :class="r.status==='Active'?'teal':r.enabled?'amber':'gray'"><i/>{{r.status}}</span></td></template>
        <template v-else-if="page==='groups'"><td>{{r.region||'—'}}</td><td>{{r.members}}</td><td><span class="badge" :class="r.enabled?'teal':'gray'"><i/>{{r.enabled?'Enabled':'Paused'}}</span></td></template>
        <template v-else-if="page==='products'"><td>{{r.sku}}</td><td>{{r.category||'—'}}</td><td><span class="badge" :class="r.enabled?'teal':'gray'"><i/>{{r.enabled?'Enabled':'Disabled'}}</span></td></template>
        <template v-else><td>{{r.telegram_id}}</td><td>{{r.scope}}</td><td>{{r.regional?'Regional':'Dealer group'}}</td></template>
        <td><div class="row-actions"><button v-if="page==='roster'" class="icon action-icon" :aria-label="'Edit '+r.name" title="Edit member" @click="edit(r)"><Pencil :size="15"/></button><button v-if="page==='roster'" class="icon action-icon danger-action" :aria-label="'Delete '+r.name" title="Delete member" @click="remove(r)"><Trash2 :size="15"/></button><a v-if="page==='groups'" class="btn group-al-link" :href="'#annual-leave/'+r.id"><CalendarClock :size="14"/>AL</a><button class="icon action-icon" :aria-label="'Manage '+r.name" @click="manage(r)"><MoreHorizontal :size="17"/></button></div></td>
      </tr></tbody></table></div>
      <div v-if="!visible.length" class="empty"><component :is="icons[page]"/><strong>{{rows.length?'No matching records':'No '+labels[page]+' yet'}}</strong><p>{{rows.length?'Change your search or filters.':'Use the action above to add your first record.'}}</p></div>
      <div v-if="filtered.length" class="pagination"><span>Page {{currentPage}} of {{pages}}</span><button class="icon" aria-label="Previous page" :disabled="currentPage<=1" @click="currentPage--"><ChevronLeft/></button><button class="icon" aria-label="Next page" :disabled="currentPage>=pages" @click="currentPage++"><ChevronRight/></button></div>
  </section>
  <template v-else-if="page==='schedules'">
    <section class="panel padded"><div class="panel-head"><div><h2>Shift windows</h2><p>{{s.data.workdays}} · {{s.data.timezone}}</p></div><CalendarClock class="quiet"/></div>
      <div class="shift-tabs"><button v-for="(f,i) in s.data.schedules" :key="f.key" :class="{on:shift===i}" @click="shift=i">{{f.label}}</button></div>
      <div class="windows"><div v-for="w in s.data.schedules[shift].windows" :key="w.start"><Clock3 :size="15"/><strong>{{w.start}}</strong><span>–</span>{{w.end}}</div></div>
      <a class="text-link" href="#roster">Change a member’s shift <ChevronRight :size="14"/></a>
    </section>
  </template>
  <template v-else-if="page==='settings'">
    <section v-if="s.data.master_date" class="panel padded settings-panel"><div class="panel-head"><div><h2>SA reporting workflow</h2><p>Master roster · {{s.data.master_date}}</p></div></div><p>Send the outlet report with photos, then reply to your own report with a Telegram location pin. Include your employee ID, dealer, outlet details and product quantities in the report.</p></section>
    <section class="panel padded settings-panel"><div class="panel-head"><div><h2>Alert delivery</h2><p>Control scheduled missing-report notifications across groups.</p></div><Bell class="quiet"/></div>
      <div class="setting-row"><span class="icon-tile teal"><Radio :size="19"/></span><div><strong>Scheduled alerts</strong><small>{{s.data.alerts_enabled?'Scheduled checks can send Telegram alerts.':'Scheduled notifications are paused.'}}</small></div>
        <button class="toggle" role="switch" aria-label="Scheduled alerts" :aria-checked="s.data.alerts_enabled" @click="s.form={type:'monitoring'}"><i/></button></div>
      <div class="setting-row"><span class="icon-tile violet"><Clock3 :size="19"/></span><div><strong>Bot timezone</strong><small>{{s.data.timezone}}</small></div></div>
    </section>
    <section class="panel padded settings-panel"><div class="panel-head"><div><h2>Controller alerts</h2><p>Send a controller-labeled copy when a missing alert is delivered to its SA group.</p></div><ShieldCheck class="quiet"/></div>
      <form class="controller-alert-settings" @submit.prevent="saveControllerAlerts"><label>SA_Controll group ID<input v-model.trim="controllerGroupId" inputmode="numeric" required aria-label="Controller alert group ID"></label>
        <label class="controller-alert-check"><input type="checkbox" v-model="controllerAlertsEnabled">Send controller copies</label>
        <button class="primary" :disabled="settingsBusy">{{settingsBusy?'Saving…':'Save controller alerts'}}</button></form>
      <p v-if="settingsError" class="error-text" role="alert">{{settingsError}}</p><p class="form-hint">The bot must be a member of the destination group. Delivery errors are logged; alerts to SA groups continue.</p>
    </section>
    <section class="panel padded settings-panel"><div class="panel-head"><div><h2>Workspace access</h2><p>{{s.preview?'This preview uses fictional data and an isolated database.':s.authRequired?'Management administrators sign in with a private, one-time Telegram code.':'This local dashboard opens directly without a sign-in code.'}}</p></div><ShieldCheck class="quiet"/></div>
      <div class="setting-row"><span class="icon-tile blue"><Database :size="19"/></span><div><strong>{{s.preview?'Preview database':'Operations database'}}</strong><small>{{s.preview?'Changes last for this preview session.':'Roster, product, and alert changes are saved in PostgreSQL.'}}</small></div></div>
    </section>
  </template>
</template>
