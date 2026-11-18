<script setup>
import {computed, nextTick, onMounted, onUnmounted, reactive, ref} from 'vue'
import {ArrowDownToLine, Bell, Boxes, Check, ChevronRight, Clock3, Layers3, Pencil, Power, RefreshCw, RotateCcw, ShieldCheck, Trash2, Users, X} from 'lucide-vue-next'
import {positions,shifts,useWorkspace} from './workspace'
const {s,save,download}=useWorkspace()
const source=s.form, row=source.row||{}, type=ref(source.type), err=ref(''), dialog=ref(null)
const controllerChoices=computed(()=>[...new Map(s.data.controllers.filter(c=>Number(c.telegram_id)>0).map(c=>[String(c.telegram_id),c])).values()].sort((a,b)=>a.name.localeCompare(b.name)))
const values=reactive({name:source.type==='memberController'?(row.controller_name||''):(row.name||''),sku:row.sku||'',category:row.category||'',position:row.position||positions[0],shift:row.shift||shifts[1],
  group_id:source.type==='group'?'':row.group_id||s.data.groups[0]?.id||'',telegram_id:source.type==='memberController'?(row.controller_telegram_id||''):(row.telegram_id||''),employee_id:row.employee_id||'',dealer:row.dealer||'',period:source.period||({AL_DAY:'day',AL_MORNING:'morning',AL_AFTERNOON:'afternoon',AL_RANGE:'range'}[row.leave_reason]||'day'),scope:source.type==='memberController'?((row.region||'')+'-'+(row.dealer||'')):(row.scope||''),username:source.type==='memberController'?(row.controller_username||''):(row.username||''),
  kind:source.kind||'missing',day:s.data.now.slice(0,10),start_date:row.leave_start?.slice(0,10)||s.data.now.slice(0,10),end_date:row.leave_end?.slice(0,10)||s.data.now.slice(0,10),region:'all',member_region:row.region||'R1',controller_id:''})
const titles={product:row.id?'Edit product':'Add product',member:'Add member',manageMember:'Manage member',editMember:'Edit member',memberController:'Edit dealer controller',
  leave:'Set annual leave',group:'Register Telegram group',manageGroup:'Manage Telegram group',controller:'Assign controller',
  manageController:'Manage controller',export:'Export report',monitoring:s.data.alerts_enabled?'Pause scheduled alerts?':'Resume scheduled alerts?',
  toggleProduct:row.enabled?'Disable product?':'Enable product?',toggleMember:row.enabled?'Disable member?':'Restore member?',
  toggleGroup:row.enabled?'Pause group alerts?':'Resume group alerts?',reset:'Reset current locations?',check:'Send missing alerts?',
  unassign:row.regional?'Remove regional assignment?':'Remove dealer assignment?',deleteMember:'Delete SA from roster?',deleteProduct:'Delete product?'}
const title=computed(()=>titles[type.value])
const actions=computed(()=>({
  manageMember:[['editMember','Edit all SA details',Pencil],['memberController','Edit dealer controller',ShieldCheck],...(row.bound&&row.enabled?[['leave','Manage annual leave',Clock3]]:[]),['toggleMember',row.enabled?'Disable member':'Restore member',Power],['deleteMember','Delete SA',Trash2]],
  manageGroup:[['sync','Synchronize roster',RefreshCw],['toggleGroup',row.enabled?'Pause group alerts':'Resume group alerts',Power],['reset','Reset current locations',RotateCcw],
    ...(row.enabled&&!s.preview?[['check','Send missing alerts',Bell]]:[])],
  manageController:[['controller',row.regional?'Replace regional controller':'Assign group controller',Pencil],['unassign','Remove assignment',Power]],
}[type.value]||[]))
const copy=computed(()=>({
  toggleProduct:'The catalog will show '+row.name+' as '+(row.enabled?'disabled.':'enabled.'),
  toggleGroup:'Scheduled checks for '+row.name+' will '+(row.enabled?'pause.':'resume.'),
  toggleMember:'Monitoring for '+row.name+' will '+(row.enabled?'stop until restored.':'resume for eligible reporting windows.'),
  monitoring:'This changes scheduled alert delivery for all registered groups. Field reporting continues.',
  reset:'This clears the latest stored location for every member in '+row.name+'. Pin history is retained.',
  check:'The bot will check the latest completed windows and message this Telegram group about missing submissions.',
  unassign:row.regional?'This clears all regional controllers for '+row.scope+'. Exact dealer assignments remain active.':'This removes '+row.name+' from '+row.scope+'.',
  deleteMember:'Remove '+row.name+' from the active roster and scheduled alerts? Past reports and location pins remain available.',
  deleteProduct:'Remove '+row.name+' ('+row.sku+') from the product catalog?',
}[type.value]||''))
const previous=document.activeElement
function close(){if(!s.busy)s.form=null}
function keydown(e){
  if(e.key==='Escape'){e.preventDefault();close()}
  if(e.key==='Tab'){
    const list=[...dialog.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')].filter(el=>el.offsetParent!==null)
    if(!list.length)return
    if(e.shiftKey&&document.activeElement===list[0]){e.preventDefault();list.at(-1).focus()}
    else if(!e.shiftKey&&document.activeElement===list.at(-1)){e.preventDefault();list[0].focus()}
  }
}
onMounted(()=>{document.addEventListener('keydown',keydown);dialog.value.querySelector('input,select,button')?.focus()})
onUnmounted(()=>{document.removeEventListener('keydown',keydown);previous?.focus()})
async function choose(next){
  err.value=''
  if(next==='sync'){await run(()=>save('groups/'+row.id+'/sync','POST'));return}
  if(next==='memberController'){
    values.scope=(row.region||'')+'-'+(row.dealer||'')
    const existing=s.data.controllers.find(c=>c.scope===values.scope)
    values.name=existing?.name||'';values.telegram_id=existing?.telegram_id||'';values.username=existing?.username||''
    type.value='memberController'
  }else type.value=next
  await nextTick();dialog.value.querySelector('input,select,button')?.focus()
}
async function run(fn){
  if(s.busy)return
  s.busy=true;err.value=''
  try{await fn();s.form=null}catch(e){err.value=e.message}finally{s.busy=false}
}
async function submit(){
  const v=values
  await run(async()=>{
    switch(type.value){
      case 'export': return download(v.kind,v.day,v.region)
      case 'product': return save(row.id?'products/'+row.id:'products',row.id?'PUT':'POST',{name:v.name,sku:v.sku,category:v.category},'Product saved.')
      case 'member': return save('members','POST',{position:v.position,shift:v.shift,group_id:v.group_id,telegram_id:v.telegram_id,employee_id:v.employee_id,dealer:v.dealer,controller_id:v.controller_id},'Member added.')
      case 'editMember':return save('members/'+row.id,'PUT',{name:v.name,username:v.username,telegram_id:v.telegram_id,employee_id:v.employee_id,
        region:v.member_region,dealer:v.dealer,position:v.position,shift:v.shift,group_id:v.group_id},'Member updated.')
      case 'memberController':return save('members/'+row.id+'/controller','PUT',
        {name:v.name,username:v.username,telegram_id:v.telegram_id},'Dealer controller updated.')
      case 'deleteMember':return save('members/'+row.id,'DELETE',undefined,'Member deleted.')
      case 'deleteProduct':return save('products/'+row.id,'DELETE',undefined,'Product deleted.')
      case 'leave':return save('members/'+row.id+'/leave','PUT',{period:v.period,start_date:v.period==='off'?null:v.start_date,
        end_date:v.period==='range'?v.end_date:null},'Leave updated.')
      case 'group':return save('groups/register/'+v.group_id,'POST')
      case 'controller':return save('controllers','PUT',{name:v.name,telegram_id:v.telegram_id,scope:v.scope,username:v.username},'Controller assigned.')
      case 'unassign':return save('controllers/'+encodeURIComponent(row.scope)+'/'+row.telegram_id,'DELETE',undefined,'Assignment removed.')
      case 'monitoring':return save('monitoring','PUT',{enabled:!s.data.alerts_enabled})
      case 'reset':case 'check':return save('groups/'+row.id+'/'+type.value,'POST')
      default:{
        const path={toggleProduct:'products',toggleGroup:'groups',toggleMember:'members'}[type.value]
        return save(path+'/'+row.id+'/status','PUT',{enabled:!row.enabled})
      }
    }
  })
}
</script>
<template><Teleport to="body"><div class="dialog-overlay" @click.self="close"><section ref="dialog" class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
  <div class="dialog-head"><div><span v-if="row.name" class="dialog-context">{{row.name}}</span><h2 id="dialog-title">{{title}}</h2></div><button class="icon" aria-label="Close dialog" :disabled="s.busy" @click="close"><X :size="18"/></button></div>
  <div v-if="actions.length" class="action-list"><button v-for="[key,label,Icon] in actions" :key="key" :disabled="s.busy" @click="choose(key)"><component :is="Icon" :size="17"/>{{label}}<ChevronRight class="ml-auto" :size="15"/></button></div>
  <form v-else @submit.prevent="submit">
    <div v-if="copy" class="dialog-copy">{{copy}}</div>
    <template v-if="type==='product'"><label>Product name<input v-model.trim="values.name" required maxlength="160"></label><label>SKU<input v-model.trim="values.sku" required maxlength="64"></label><label>Category <small>(optional)</small><input v-model.trim="values.category" maxlength="80"></label>
      <button v-if="row.id" type="button" class="text-link" @click="choose('toggleProduct')"><Power :size="14"/>{{row.enabled?'Disable product':'Enable product'}}</button></template>
    <template v-if="type==='member'"><label>Telegram group<select v-model="values.group_id" required><option v-for="g in s.data.groups" :key="g.id" :value="g.id">{{g.name}}</option></select></label>
      <p v-if="!s.data.groups.length" class="error-text">Register a Telegram group first.</p><label>Telegram user ID<input v-model.trim="values.telegram_id" inputmode="numeric" pattern="[1-9][0-9]*" required></label><label>Employee ID<input v-model.trim="values.employee_id" maxlength="64" placeholder="e.g. EMP001"></label><label>Dealer<input v-model.trim="values.dealer" placeholder="CA1" maxlength="50" required></label>
      <label>Controller<select v-model="values.controller_id" required><option value="" disabled>Choose controller</option><option v-for="c in controllerChoices" :key="c.telegram_id" :value="c.telegram_id">{{c.name}}</option></select></label>
      <p class="form-hint">The selected controller will manage every SA in this region and dealer.</p></template>
    <template v-if="type==='editMember'"><label>Telegram ID<input v-model.trim="values.telegram_id" inputmode="numeric" pattern="[1-9][0-9]*" required></label>
      <label>Employee ID<input v-model.trim="values.employee_id" maxlength="64" placeholder="e.g. EMP001"></label>
      <label>SA name<input v-model.trim="values.name" maxlength="255" required></label><label>Username<input v-model.trim="values.username" maxlength="255" placeholder="@username"></label>
      <label>Region<select v-model="values.member_region"><option v-for="n in 8" :key="n">{{'R'+n}}</option></select></label>
      <label>Dealer<input v-model.trim="values.dealer" maxlength="50" required></label>
      <label>Telegram group<select v-model="values.group_id" required><option v-for="g in s.data.groups" :key="g.id" :value="g.id">{{g.name}}</option></select></label></template>
    <template v-if="['member','editMember'].includes(type)"><label>Position<select v-model="values.position"><option v-for="p in positions" :key="p">{{p}}</option></select></label><label>Working time<select v-model="values.shift"><option v-for="p in shifts" :key="p">{{p}}</option></select></label></template>
    <template v-if="type==='leave'"><label>Leave type<select v-model="values.period"><option value="day">AL Full Day</option><option value="morning">AL Morning</option><option value="afternoon">AL Afternoon</option><option value="range">AL Date Range</option><option value="off">Cancel AL</option></select></label>
      <label v-if="values.period!=='off'">Start date<input v-model="values.start_date" type="date" required></label>
      <label v-if="values.period==='range'">End date <small>(inclusive)</small><input v-model="values.end_date" type="date" :min="values.start_date" required></label>
      <p v-if="values.period==='range'" class="form-hint">The bot will skip scheduled missing-report alerts from the start date through the end date.</p></template>
    <template v-if="['controller','memberController'].includes(type)"><p class="form-hint">A dealer controller assignment applies to every SA in that dealer group.</p><label>Region or dealer group<input v-model.trim="values.scope" placeholder="R1 or R1-CA1" :readonly="type==='memberController'" required maxlength="50"></label><label>Controller name<input v-model.trim="values.name" maxlength="255" required></label>
      <label>Telegram user ID<input v-model.trim="values.telegram_id" inputmode="numeric" pattern="[1-9][0-9]*" required></label><label>Telegram username<input v-model.trim="values.username" maxlength="254" :required="/^R[1-8]$/i.test(values.scope)" placeholder="@username"></label></template>
    <template v-if="type==='group'"><label>Telegram group ID<input v-model.trim="values.group_id" inputmode="text" pattern="-[0-9]+" placeholder="-100…" required></label><p class="form-hint">{{s.preview?'Registration is available in the live workspace.':'The bot must already belong to the group.'}}</p></template>
    <template v-if="type==='export'"><label>Workbook<select v-model="values.kind"><option value="missing">Missing report log</option><option value="roster">Complete SA roster</option></select></label>
      <template v-if="values.kind==='missing'"><label>Report date<input v-model="values.day" type="date" required></label><label>Region<select v-model="values.region"><option value="all">All regions</option><option v-for="n in 8" :key="n">{{'R'+n}}</option></select></label></template></template>
    <div class="dialog-actions"><button type="button" class="btn" :disabled="s.busy" @click="close">Cancel</button><button class="primary" :disabled="s.busy||(type==='group'&&s.preview)||(type==='member'&&!s.data.groups.length)"><component :is="type==='export'?ArrowDownToLine:Check" :size="15"/>{{s.busy?'Working…':type==='export'?'Download workbook':copy?'Confirm':'Save changes'}}</button></div>
  </form><p v-if="err" class="error-text" role="alert">{{err}}</p></section></div></Teleport></template>
