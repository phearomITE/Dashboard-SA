<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'
import { Activity, ArrowDownToLine, Boxes, CalendarDays, ChevronDown, ChevronRight,
  Clock3, Layers3, LayoutDashboard, ListChecks, LogOut, MapPinned, Menu, PanelLeft, Radio,
  RefreshCw, ShieldCheck, SlidersHorizontal, Users, X } from 'lucide-vue-next'
import { dateLabel, useWorkspace } from './workspace'
import Overview from './Overview.vue'
import Management from './Management.vue'
import AnnualLeave from './AnnualLeave.vue'
import MissingReport from './MissingReport.vue'
import Dialogs from './Dialogs.vue'
import brandLogo from './assets/chip-mong-foodbev.png'
const LiveMap = defineAsyncComponent(() => import('./LiveMap.vue'))

const { s, start, refresh, signIn, signOut, download, notify } = useWorkspace()
const nav = [
  ['Workspace', [['overview','Overview',LayoutDashboard],['live-map','Live Map',MapPinned],['missing-report','Miss Report',ListChecks],['roster','SA roster',Users],['annual-leave','Annual Leave',CalendarDays]]],
  ['Management', [['products','Products',Boxes],['controllers','Controllers',ShieldCheck],['schedules','Schedules',Clock3],['settings','Settings',SlidersHorizontal]]],
]
const names = Object.fromEntries(nav.flatMap(([,items]) => items.map(([k,n]) => [k,n])))
const descriptions = {overview:'A live view of your field team and reporting activity.',activity:'Review reports, location pins, and missing alerts across your groups.',
  'live-map':'Follow field check-ins and trace each employee’s route from dealer to outlet.',
  roster:'Manage field members, working shifts, and alert eligibility.',groups:'Manage the Telegram groups connected to your bot.',
  'annual-leave':'Schedule full-day, morning, afternoon, or date-range leave for SA members.',
  'missing-report':'Review SA visits, missing reports, and written reasons.',
  products:'Manage the products available in your field catalog.',controllers:'Manage regional and dealer-level controller assignments.',
  schedules:'Explore the reporting windows used by each working shift.',settings:'Manage alert delivery and workspace access.'}
const page = ref('overview'), alGroup = ref(''), mobile = ref(false), code = ref(''), loginError = ref(''), loginBusy = ref(false)
const current = computed(() => names[page.value] || names.overview)
function route() {
  const [next,filter] = location.hash.slice(1).split('/')
  page.value = Object.hasOwn(names,next) ? next : 'overview'
  alGroup.value = page.value==='annual-leave' ? (filter||'') : ''
  mobile.value = false; s.form = null; window.scrollTo(0,0)
  const kind = page.value==='activity' && ['alerts','reports','pins'].includes(filter) ? filter : 'all'
  if (s.kind!==kind || s.page!==1) { s.kind=kind; s.page=1; if (s.signedIn) refresh() }
}
function go(target) { location.hash = target; mobile.value = false }
let timer
onMounted(async () => {
  route(); window.addEventListener('hashchange', route); await start()
  timer = setInterval(() => { if (s.signedIn && !document.hidden && !s.busy && !s.loading && !s.form) refresh() }, 60000)
})
onUnmounted(() => { clearInterval(timer); window.removeEventListener('hashchange', route) })
async function login() {
  loginBusy.value=true; loginError.value=''
  try { await signIn(code.value); code.value='' } catch(e) { loginError.value=e.message } finally { loginBusy.value=false }
}
async function logout() {
  try { await signOut(); code.value=''; mobile.value=false } catch(e) { notify(e.message) }
}
async function range(e) { s.days=Number(e.target.value); s.page=1; await refresh() }
</script>
<template>
  <div v-if="!s.ready || !s.data || (s.authRequired && !s.signedIn)" class="login-page">
    <div class="login-card">
      <div class="brand"><span class="company-logo"><img :src="brandLogo" alt="Chip Mong FoodBev"></span></div>
      <div class="login-symbol"><ShieldCheck :size="34"/></div>
      <h1>{{ !s.ready ? 'Opening workspace…' : s.authRequired && !s.signedIn ? 'Workspace sign-in' : 'Workspace unavailable' }}</h1>
      <template v-if="s.ready && !s.authRequired && !s.data">
        <p class="error-text" role="alert">{{s.error || 'The dashboard could not load.'}}</p>
        <button class="primary" @click="refresh">Retry<RefreshCw :size="16"/></button>
      </template>
      <template v-else-if="s.ready">
        <p>{{ s.preview ? 'Open the sample workspace to explore the dashboard.' : 'Send /dashboard in a private chat with your Telegram bot to receive a one-time access code.' }}</p>
        <form @submit.prevent="login">
          <label v-if="!s.preview">Access code<input v-model.trim="code" required maxlength="128" autocomplete="one-time-code" placeholder="Paste your sign-in code"></label>
          <p v-if="loginError || (s.error && s.error !== 'Sign in to the workspace.')" class="error-text" role="alert">{{ loginError || s.error }}</p>
          <button class="primary" :disabled="loginBusy">{{ loginBusy ? 'Signing in…' : 'Open workspace' }}<ChevronRight :size="16"/></button>
        </form>
      </template>
    </div>
  </div>
  <div v-else class="shell">
    <button v-if="mobile" class="scrim" aria-label="Close navigation" @click="mobile=false"></button>
    <aside :class="{open:mobile}">
      <div class="brand"><span class="company-logo"><img :src="brandLogo" alt="Chip Mong FoodBev"></span><button class="mobile-only icon ml-auto" aria-label="Close navigation" @click="mobile=false"><X/></button></div>
      <div class="workspace-id"><span class="bot-icon"><Radio :size="20"/></span><div><strong>SA Alert Bot</strong><small>Operations workspace</small></div></div>
      <nav v-for="[section,items] in nav" :key="section" :aria-label="section"><h2>{{section}}</h2>
        <a v-for="[key,label,Icon] in items" :key="key" :href="'#'+key" :class="{selected:page===key}" :aria-current="page===key?'page':undefined" @click="mobile=false">
          <component :is="Icon" :size="18"/>{{label}}<span v-if="page===key" class="nav-dot"></span>
        </a>
      </nav>
      <div class="side-bottom"><span class="access-avatar">AD</span><div><strong>Administrator</strong><small>{{s.preview?'Preview workspace':'Management access'}}</small></div>
        <button v-if="s.authRequired" class="icon ml-auto" title="Sign out" aria-label="Sign out" @click="logout"><LogOut :size="16"/></button></div>
    </aside>
    <main>
      <header class="topbar"><div class="flex items-center gap-3"><button class="icon mobile-only" aria-label="Open navigation" @click="mobile=true"><Menu/></button>
        <PanelLeft class="desktop-only" :size="16"/><span class="bread-base">Workspace</span><ChevronRight :size="13"/><strong>{{current}}</strong></div>
        <div class="flex items-center gap-3"><span class="connection" :class="{sample:s.preview}"><i></i>{{s.preview?'Preview mode':s.data.online?'Bot online':'Polling offline'}}</span>
          <button class="icon refresh" title="Refresh workspace" aria-label="Refresh workspace" :disabled="s.loading" @click="refresh"><RefreshCw :size="16" :class="{spin:s.loading}"/></button></div>
      </header>
      <div class="page-content" :class="{'live-map-page':page==='live-map','compact-page':page!=='overview'}" :key="page">
        <div class="page-head"><div><div class="date-line"><span></span>{{dateLabel(s.data.now,{weekday:'long',month:'long',day:'numeric',year:'numeric'})}}</div>
          <h1>{{page==='overview'?'Operations overview':current}}</h1><p>{{descriptions[page]}}</p></div>
          <div class="page-tools"><label v-if="['overview','activity'].includes(page)" class="range-control"><CalendarDays :size="15"/>
            <select :value="s.days" aria-label="Reporting period" @change="range"><option :value="7">Last 7 days</option><option :value="14">Last 14 days</option><option :value="30">Last 30 days</option></select><ChevronDown :size="13"/></label>
            <button v-if="['overview','activity','roster'].includes(page)" class="btn" :class="{primary:page!=='roster'}" @click="page==='roster'?download('roster',s.data.now.slice(0,10),'all'):(s.form={type:'export',kind:'missing'})"><ArrowDownToLine :size="16"/>Export<span class="wide-only"> {{page==='roster'?'SA roster':'report'}}</span></button>
            <button v-if="['roster','products','controllers','groups'].includes(page)" class="primary" @click="s.form={type:({roster:'member',products:'product',controllers:'controller',groups:'group'})[page]}"><span class="plus">+</span>{{({roster:'Add member',products:'Add product',controllers:'Assign controller',groups:'Register group'})[page]}}</button></div>
        </div>
        <div v-if="s.error" class="error-banner" role="alert">{{s.error}}<button @click="refresh">Retry</button></div>
        <Overview v-if="page==='overview'||page==='activity'" :activity-only="page==='activity'" @navigate="go"/>
        <LiveMap v-else-if="page==='live-map'"/>
        <MissingReport v-else-if="page==='missing-report'"/>
        <AnnualLeave v-else-if="page==='annual-leave'" :group-id="alGroup"/>
        <Management v-else :page="page"/>
      </div>
    </main>
    <Dialogs v-if="s.form" :key="s.form.type+'-'+(s.form.row?.id||'')"/>
    <Transition><div v-if="s.notice" class="toast" role="status"><ListChecks :size="17"/>{{s.notice}}<button class="icon" aria-label="Dismiss message" @click="s.notice=''"><X :size="15"/></button></div></Transition>
  </div>
</template>
