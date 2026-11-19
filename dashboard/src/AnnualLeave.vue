<script setup>
import {computed,ref,watch} from 'vue'
import {CalendarDays,Search} from 'lucide-vue-next'
import {dateLabel,num,useWorkspace} from './workspace'
import ProfileAvatar from './ProfileAvatar.vue'
import './annual-leave.css'

const props=defineProps({groupId:{type:String,default:''}})
const {s}=useWorkspace()
const group=ref(props.groupId),query=ref(''),state=ref('all'),controller=ref(''),fromDate=ref(''),toDate=ref('')
watch(()=>props.groupId,value=>{group.value=value})
const now=computed(()=>new Date(s.data.now))
const leaveState=row=>{
  if(!row.leave_end||new Date(row.leave_end)<now.value)return 'none'
  return row.leave_start&&new Date(row.leave_start)>now.value?'scheduled':'active'
}
const leaveType=row=>({AL_DAY:'Full day',AL_MORNING:'Morning',AL_AFTERNOON:'Afternoon',AL_RANGE:'Date range'})[row.leave_reason]||'Annual leave'
const leaveDates=row=>{
  if(!row.leave_start||!row.leave_end)return '—'
  const first=dateLabel(row.leave_start.slice(0,10),{day:'numeric',month:'short',year:'numeric'})
  const last=dateLabel(row.leave_end.slice(0,10),{day:'numeric',month:'short',year:'numeric'})
  return first===last?first:first+' – '+last
}
const rows=computed(()=>s.data.roster.filter(row=>{
  const term=query.value.trim().toLocaleLowerCase()
  const ls=row.leave_start?.slice(0,10)||row.leave_end?.slice(0,10)||''
  const le=row.leave_end?.slice(0,10)||''
  const matchesDate=(!fromDate.value&&!toDate.value)||(Boolean(le)&&(!fromDate.value||le>=fromDate.value)&&(!toDate.value||ls<=toDate.value))
  return (!group.value||row.group_id===group.value)
    &&(!controller.value||row.controller===controller.value)
    &&(state.value==='all'||leaveState(row)===state.value)
    &&matchesDate
    &&(!term||[row.name,row.telegram_id,row.username,row.dealer].some(value=>String(value||'').toLocaleLowerCase().includes(term)))
}))
const active=computed(()=>s.data.roster.filter(row=>leaveState(row)==='active').length)
const scheduled=computed(()=>s.data.roster.filter(row=>leaveState(row)==='scheduled').length)
function setLeave(row,period='day'){s.form={type:'leave',row,period}}
</script>

<template>
  <section class="al-summary"><div class="panel"><strong>{{num(active)}}</strong><span>On annual leave now</span></div>
    <div class="panel"><strong>{{num(scheduled)}}</strong><span>Scheduled leave</span></div>
    <div class="panel"><strong>{{num(s.data.roster.length)}}</strong><span>SA in roster</span></div></section>
  <section class="panel records al-records">
    <div class="record-tools"><label class="search-box"><Search :size="16"/><input v-model="query" aria-label="Search SA for annual leave" placeholder="Search SA or Telegram ID…"></label>
      <label class="al-date-filter"><span>From Date</span><input type="date" v-model="fromDate" :max="toDate||undefined" class="control"></label>
      <label class="al-date-filter"><span>To Date</span><input type="date" v-model="toDate" :min="fromDate||undefined" class="control"></label>
      <select v-model="group" class="control" aria-label="Telegram group filter"><option value="">All Telegram groups</option><option v-for="g in s.data.groups" :key="g.id" :value="g.id">{{g.name}}</option></select>
      <select v-model="controller" class="control" aria-label="Controller filter"><option value="">All controllers</option><option v-for="name in [...new Set(s.data.roster.map(r=>r.controller).filter(Boolean))].sort()" :key="name">{{name}}</option></select>
      <select v-model="state" class="control" aria-label="Annual leave status"><option value="all">All SA</option><option value="active">On leave</option><option value="scheduled">Scheduled</option><option value="none">No leave</option></select>
      <button v-if="fromDate||toDate" type="button" class="btn" @click="fromDate='';toDate=''">Clear dates</button>
      <span class="record-count">{{num(rows.length)}} members</span></div>
    <div class="table-scroll"><table class="al-table"><thead><tr><th>SA Name</th><th>Telegram ID</th><th>Group</th><th>Controller</th><th>Leave type</th><th>Dates</th><th>Status</th><th>Action</th></tr></thead>
      <tbody><tr v-for="row in rows" :key="row.id"><td><div class="person"><ProfileAvatar :telegram-id="row.telegram_id" :name="row.name"/><span><strong>{{row.name}}</strong><small>{{row.position}} · {{row.dealer}}</small></span></div></td>
        <td>{{row.telegram_id||'—'}}</td><td>{{row.group}}</td><td>{{row.controller||'—'}}</td><td>{{leaveState(row)==='none'?'—':leaveType(row)}}</td><td>{{leaveState(row)==='none'?'—':leaveDates(row)}}</td>
        <td><span class="badge" :class="leaveState(row)==='active'?'amber':leaveState(row)==='scheduled'?'teal':'gray'"><i/>{{leaveState(row)==='active'?'On leave':leaveState(row)==='scheduled'?'Scheduled':'No leave'}}</span></td>
        <td><div class="row-actions"><button class="btn" :disabled="!row.bound||!row.enabled" @click="setLeave(row)"><CalendarDays :size="14"/>{{leaveState(row)==='none'?'Set AL':'Edit AL'}}</button>
          <button v-if="leaveState(row)!=='none'" class="btn" @click="setLeave(row,'off')">Cancel AL</button></div></td></tr></tbody></table></div>
    <div v-if="!rows.length" class="empty"><CalendarDays/><strong>No matching SA</strong><p>Change the group, status, or search filter.</p></div>
  </section>
</template>
