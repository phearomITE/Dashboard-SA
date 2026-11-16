<script setup>
import {computed,onMounted,onUnmounted,reactive,ref,watch} from 'vue'
import {ArrowLeft,ArrowRight,Building2,ChevronRight,Download,LocateFixed,MapPin,RefreshCw,Route,Search,Upload,Users,X} from 'lucide-vue-next'
import {initials,num,useWorkspace} from './workspace'
import CheckinMap from './CheckinMap.vue'
import ProfileAvatar from './ProfileAvatar.vue'
import './live-map.css'

const {s,request,notify}=useWorkspace()
const today=ref(s.data.now.slice(0,10)),day=ref(today.value)
const records=ref([]),reportRecords=ref([]),missRecords=ref([]),dealers=ref([]),error=ref(''),loading=ref(false),firstLoad=ref(true)
const selected=ref(''),focused=ref(null),search=ref(''),dialog=ref(null),file=ref(null),uploadError=ref(''),uploading=ref(false)
const reasons=ref({})
const clock=stamp=>new Date(stamp).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})
const member=computed(()=>s.data.roster.find(r=>r.telegram_id===selected.value&&r.region===employee.value?.last.region&&r.dealer===employee.value?.last.dealer))
const dayUsers=computed(()=>new Set(records.value.map(p=>p.employee_id)).size)
const routeKm=computed(()=>events.value.some(p=>p.leg_km==null)?'Incomplete':km(events.value.reduce((n,p)=>n+p.leg_km,0)))
const filters=reactive({region:'',dealer:'',province:'',district:'',commune:''})
const fields=[['region','Region'],['dealer','Dealer'],['province','Province'],['district','District'],['commune','Commune']]
const scope=p=>JSON.stringify([p.region,p.dealer])
const km=value=>value==null?'—':value.toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:2})+' km'
const unique=values=>[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}))
const originByScope=computed(()=>new Map(dealers.value.map(d=>[scope(d),d])))
function options(key){
  const parents={dealer:['region'],district:['province'],commune:['province','district']}[key]||[]
  return unique(records.value.filter(p=>parents.every(k=>!filters[k]||p[k]===filters[k])).map(p=>p[key]))
}
function changeFilter(key,value){
  filters[key]=value
  if(key==='region')filters.dealer=''
  if(key==='province')filters.district=''
  if(key==='province'||key==='district')filters.commune=''
  focused.value=null
}
const filtered=computed(()=>{
  const term=search.value.trim().toLocaleLowerCase()
  return records.value.filter(p=>fields.every(([k])=>!filters[k]||filters[k]===p[k])
    && (!term||[p.name,p.username,p.employee_id,p.reported_employee_id].some(v=>String(v||'').toLocaleLowerCase().includes(term))))
})
const coverage=computed(()=>({
  provinces:new Set(filtered.value.map(p=>p.province).filter(Boolean)).size,
  districts:new Set(filtered.value.filter(p=>p.district).map(p=>[p.province,p.district].join('|'))).size,
  communes:new Set(filtered.value.filter(p=>p.commune).map(p=>[p.province,p.district,p.commune].join('|'))).size,
}))
const employees=computed(()=>{
  const found=new Map()
  for(const p of filtered.value){
    const previous=found.get(p.employee_id)
    found.set(p.employee_id,{id:p.employee_id,name:p.name,last:p,count:(previous?.count||0)+1})
  }
  return [...found.values()].sort((a,b)=>b.last.at.localeCompare(a.last.at)||a.name.localeCompare(b.name))
})
const rosterPool=computed(()=>{
  const term=search.value.trim().toLocaleLowerCase()
  return (s.data?.roster||[]).filter(r=>(!filters.region||r.region===filters.region)&&(!filters.dealer||r.dealer===filters.dealer)
    &&(!selected.value||r.telegram_id===selected.value)
    &&(!term||[r.name,r.username,r.telegram_id].some(v=>String(v||'').toLocaleLowerCase().includes(term))))
})
const isOnLeave=r=>Boolean(r.leave_end&&r.leave_end.slice(0,10)>=day.value&&(!r.leave_start||r.leave_start.slice(0,10)<=day.value))
const checkInSaCount=computed(()=>selected.value?(events.value.length?1:0):employees.value.length)
const alCount=computed(()=>rosterPool.value.filter(isOnLeave).length)
const missSaCount=computed(()=>Math.max(0,rosterPool.value.length-checkInSaCount.value-alCount.value))
const employee=computed(()=>employees.value.find(e=>e.id===selected.value))
const events=computed(()=>employee.value?filtered.value.filter(p=>p.employee_id===selected.value):filtered.value)
const routes=computed(()=>{
  const byScope=new Map()
  for(const p of events.value){
    const key=scope(p)
    if(!byScope.has(key))byScope.set(key,{region:p.region,dealer:p.dealer,origin:originByScope.value.get(key),stops:[]})
    byScope.get(key).stops.push(p)
  }
  return [...byScope.values()]
})
const origins=computed(()=>employee.value?routes.value.map(r=>r.origin).filter(Boolean):dealers.value.filter(d=>(!filters.region||d.region===filters.region)&&(!filters.dealer||d.dealer===filters.dealer)))
const fitKey=computed(()=>JSON.stringify([day.value,selected.value,search.value,filters,events.value.length>0,origins.value]))
const hasFilters=computed(()=>search.value||Object.values(filters).some(Boolean))
function clear(){for(const [k] of fields)filters[k]='';search.value='';selected.value='';focused.value=null}
function selectEmployee(id){selected.value=id;focused.value=null}
watch(employees,list=>{if(selected.value&&!list.some(e=>e.id===selected.value))selectEmployee('')})
let serial=0,timer
async function load(reset=false){
  const run=++serial,requested=day.value
  if(reset){records.value=[];reportRecords.value=[];missRecords.value=[];firstLoad.value=true;clear()}
  if(!requested){error.value='Choose a reporting date.';loading.value=false;return}
  loading.value=true;error.value=''
  try{
    const result=await request('live-map?day='+encodeURIComponent(requested))
    if(run!==serial)return
    const current=result.now.slice(0,10),followToday=requested===today.value
    today.value=current
    if(followToday&&requested!==current){day.value=current;return}
    records.value=result.events;reportRecords.value=result.reports||[];missRecords.value=result.misses||[];dealers.value=result.dealers;reasons.value=result.reasons||{}
  }catch(e){if(run===serial)error.value=e.message}
  finally{if(run===serial){loading.value=false;firstLoad.value=false}}
}
watch(day,()=>load(true))
function poll(){if(!document.hidden&&day.value===today.value&&!loading.value&&!uploading.value)load()}
onMounted(()=>{load();timer=setInterval(poll,10000);document.addEventListener('visibilitychange',poll)})
onUnmounted(()=>{serial++;clearInterval(timer);document.removeEventListener('visibilitychange',poll)})
function openImport(){file.value=null;uploadError.value='';dialog.value.querySelector('input').value='';dialog.value.showModal()}
async function upload(){
  const f=file.value
  if(!f)return
  const ext=f.name.split('.').at(-1).toLowerCase()
  if(!['csv','xlsx'].includes(ext)||f.size>4000000){uploadError.value='Choose a CSV or XLSX file under 4 MB.';return}
  uploading.value=true;uploadError.value=''
  try{
    const result=await request('live-map/dealers?format='+ext,'POST',f)
    dialog.value.close();await load();notify(result.count+' dealer location'+(result.count===1?'':'s')+' imported.')
  }catch(e){uploadError.value=e.message}finally{uploading.value=false}
}
function template(){
  const blob=new Blob(['\uFEFFRegion,Dealer,Latitude,Longitude\r\n'],{type:'text/csv;charset=utf-8'})
  const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='dealer-locations.csv';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000)
}
</script>
<template>
  <section class="live-map-section">
    <div class="map-filter-panel panel">
      <div class="map-filter-grid"><label class="day-field">Date<input type="date" v-model="day" :max="today"></label>
        <label v-for="[key,label] in fields.slice(0,2)" :key="key">{{label}}
          <select :aria-label="label" :value="filters[key]" @change="changeFilter(key,$event.target.value)"><option value="">All {{({region:'regions',dealer:'dealers'})[key]}}</option>
            <option v-for="v in options(key)" :key="v">{{v}}</option></select></label>
        <div class="map-search"><label for="employee-search">Search employee</label><div><Search :size="15"/><input id="employee-search" type="search" v-model="search" placeholder="Name, username, or ID"></div></div>
        <label v-for="[key,label] in fields.slice(2)" :key="key">{{label}}
        <select :aria-label="label" :value="filters[key]" @change="changeFilter(key,$event.target.value)"><option value="">All {{({region:'regions',dealer:'dealers',province:'provinces',district:'districts',commune:'communes'})[key]}}</option>
          <option v-for="v in options(key)" :key="v">{{v}}</option></select></label>
        <div class="map-filter-actions"><button v-if="hasFilters" class="map-back" @click="clear">Clear filters</button>
          <button class="btn upload-dealers" @click="openImport"><Upload :size="15"/>Dealer locations</button></div>
      </div>
    </div>
    <div class="map-coverage" aria-label="Filtered geographic coverage"><div><strong>{{num(checkInSaCount)}} <small>/ {{num(rosterPool.length)}}</small></strong><span>Total Check In</span></div><div><strong>{{num(alCount)}} <small>/ {{num(rosterPool.length)}}</small></strong><span>AL</span></div><div><strong>{{num(missSaCount)}} <small>/ {{num(rosterPool.length)}}</small></strong><span>Total SA Miss Report</span></div><div><strong>{{coverage.provinces}} <small>/ 25</small></strong><span>Provinces</span></div><div><strong>{{coverage.districts}} <small>/ 210</small></strong><span>Districts</span></div><div><strong>{{coverage.communes}} <small>/ 1651</small></strong><span>Communes</span></div></div>
    <div v-if="error" class="error-banner" role="alert">{{error}} <button @click="load()">Retry</button></div>
    <section class="map-board panel">
      <div class="map-pane">
        <div class="map-panel-head"><div class="map-heading"><MapPin :size="19"/><h2>Check-in map</h2></div>
          <div class="map-head-actions"><span v-if="day===today" class="map-live"><i/>{{s.preview?'Sample day':'Live · 10s'}}</span>
            <span v-else class="map-historical">Historical</span><button class="icon" aria-label="Refresh check-ins" :disabled="loading" @click="load()"><RefreshCw :size="15" :class="{spin:loading}"/></button></div></div>
        <CheckinMap :events="events" :origins="origins" :selected="selected" :focused="focused" :fit-key="fitKey" @select="selectEmployee" @focus="focused=$event"/>
        <div class="map-footer"><span><i class="legend-stop"/>Outlet check-in</span><span><i class="legend-dealer"/>Dealer origin</span><span class="map-timezone">{{s.data.timezone}}</span></div>
      </div>
      <div class="route-panel">
        <template v-if="employee"><div class="route-head"><button class="map-back" @click="selectEmployee('')"><ArrowLeft :size="14"/>All employees</button><h2>{{employee.name}}</h2>
          <p v-if="employee.last.reported_employee_id">Reported employee ID {{employee.last.reported_employee_id}}</p><p>Telegram ID {{selected}} · {{member?.position||'Position unavailable'}}</p></div>
          <div class="route-scroll"><div class="route-summary"><strong>Employee route · {{events.length}} stops</strong>
            <p>{{dayUsers}} employees on this date · {{new Set(events.filter(p=>p.district).map(p=>p.province+'|'+p.district)).size}} districts · {{new Set(events.filter(p=>p.commune).map(p=>[p.province,p.district,p.commune].join('|'))).size}} communes</p>
            <p>Route distance · {{routeKm}}</p><p>First {{clock(events[0].at)}} · Latest {{clock(events.at(-1).at)}}</p><p>Status · Checked in</p>
            <strong>Written reasons</strong><p v-for="reason in reasons[selected]||[]" :key="reason">{{reason}}</p><p v-if="!reasons[selected]?.length">No written explanation linked for this date.</p></div>
            <div v-for="r in routes" :key="scope(r)" class="employee-route">
            <div class="route-item origin"><span class="route-number"><Building2 :size="15"/></span><div><strong>Dealer · {{r.dealer||'Unassigned'}}</strong><small>{{r.region||'Region unknown'}} · {{r.origin?'Route origin':'Coordinates missing'}}</small>
              <button v-if="!r.origin" class="map-back" @click="openImport">Upload location <ArrowRight :size="12"/></button></div></div>
            <button v-for="p in r.stops" :key="p.id" class="route-item stop" :class="{active:focused===p.id}" @click="focused=p.id">
              <span class="route-number">{{p.outlet}}</span><span><strong>{{p.outlet===1?'Dealer '+(r.dealer||''):'Outlet '+(p.outlet-1)}} → Outlet {{p.outlet}}</strong><small>{{clock(p.at)}} · {{[p.commune,p.district,p.province].filter(Boolean).join(', ')||'Outside mapped areas'}}</small>
              <strong v-if="p.outlet_name">{{p.outlet_name}}</strong><small>{{km(p.leg_km)}} from {{p.outlet===1?'dealer':'previous stop'}}</small>
              <small v-if="focused===p.id">{{p.lat.toFixed(5)}}, {{p.lon.toFixed(5)}} · {{p.group}}</small><small>Show on map ↗</small></span></button>
          </div></div><div class="route-foot"><Route :size="15"/>Straight-line distances between pins</div>
        </template>
        <template v-else><div class="route-head"><div class="map-heading"><Users :size="18"/><h2>Employees</h2><span class="count-pill">{{employees.length}}</span></div>
          <p>{{filtered.length}} check-ins · latest activity first</p></div>
          <div v-if="employees.length" class="route-scroll"><button v-for="e in employees" :key="e.id" class="map-employee" @click="selectEmployee(e.id)">
            <ProfileAvatar class="map-avatar" :telegram-id="e.id" :name="e.name"/><span class="employee-label"><strong>{{e.name}}</strong><small>{{e.last.region}} · {{e.last.dealer||'Unassigned'}} · {{e.count}} stop{{e.count===1?'':'s'}}</small></span>
            <span class="map-employee-end"><time>{{e.last.at.slice(11,16)}}</time><ChevronRight :size="13"/></span></button></div>
          <div v-else class="map-empty"><LocateFixed :size="26"/><strong>{{firstLoad&&loading?'Loading check-ins…':error?'Check-ins unavailable':'No matching check-ins'}}</strong><button v-if="hasFilters" class="map-back" @click="clear">Clear filters</button></div>
        </template>
      </div>
    </section>
  </section>
  <dialog ref="dialog" class="dealer-dialog" @cancel="uploading&&$event.preventDefault()">
    <form @submit.prevent="upload"><div class="dialog-head"><h2>Upload dealer locations</h2><button type="button" class="icon" aria-label="Close upload" :disabled="uploading" @click="dialog.close()"><X :size="18"/></button></div>
      <p>Upload a CSV or Excel workbook with Region, Dealer, Latitude, and Longitude. Matching region and dealer codes update the existing origin.</p>
      <button type="button" class="map-back" @click="template"><Download :size="14"/>Download CSV template</button>
      <label>Dealer location file<input type="file" accept=".csv,.xlsx" required @change="file=$event.target.files[0];uploadError=''"></label>
      <div v-if="uploadError" class="error-text" role="alert">{{uploadError}}</div>
      <div class="dialog-actions"><button type="button" class="btn" :disabled="uploading" @click="dialog.close()">Cancel</button><button class="primary" :disabled="!file||uploading"><Upload :size="15"/>{{uploading?'Importing…':'Import locations'}}</button></div>
    </form>
  </dialog>
</template>
