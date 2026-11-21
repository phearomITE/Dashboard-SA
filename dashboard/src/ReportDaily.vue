<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Download, FileSpreadsheet, RefreshCw, Search } from 'lucide-vue-next'
import { num, useWorkspace } from './workspace'
import './report-daily.css'

const { s, request } = useWorkspace()
const today = ref(s.data.now.slice(0, 10))
const fromDate = ref(today.value)
const toDate = ref(today.value)
const regionFilter = ref('')
const dealerFilter = ref('')
const search = ref('')
const viewMode = ref('all')

const payload = ref(null)
const loading = ref(false)
const error = ref('')

const skus = computed(() => payload.value?.skus || [])
const allRegions = computed(() => payload.value?.regions || [])
const allDealers = computed(() => payload.value?.dealers || [])

const dealerOptions = computed(() => {
  const list = allDealers.value
    .filter(r => !regionFilter.value || r.region === regionFilter.value)
    .map(r => r.dealer)
  return [...new Set(list)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
})

watch(regionFilter, () => {
  if (dealerFilter.value && !dealerOptions.value.includes(dealerFilter.value)) {
    dealerFilter.value = ''
  }
})

const filteredRegions = computed(() => {
  const term = search.value.trim().toLowerCase()
  return allRegions.value.filter(r => {
    if (regionFilter.value && r.region !== regionFilter.value) return false
    if (term && !r.region.toLowerCase().includes(term)) return false
    return true
  })
})

const filteredDealers = computed(() => {
  const term = search.value.trim().toLowerCase()
  return allDealers.value.filter(r => {
    if (regionFilter.value && r.region !== regionFilter.value) return false
    if (dealerFilter.value && r.dealer !== dealerFilter.value) return false
    if (term && !`${r.region} ${r.dealer}`.toLowerCase().includes(term)) return false
    return true
  })
})

function summarize(rows, label) {
  const out = {
    region: label,
    dealer: '',
    sa: 0,
    sa_ws: 0,
    commando: 0,
    sa_leader: 0,
    al: 0,
    active: 0,
    district: 0,
    commune: 0,
    outlets_visit: 0,
    avg_outlets_visit: 0,
    skus: Object.fromEntries(skus.value.map(k => [k, 0])),
  }
  for (const r of rows) {
    out.sa += r.sa || 0
    out.sa_ws += r.sa_ws || 0
    out.commando += r.commando || 0
    out.sa_leader += r.sa_leader || 0
    out.al += r.al || 0
    out.active += r.active || 0
    out.district += r.district || 0
    out.commune += r.commune || 0
    out.outlets_visit += r.outlets_visit || 0
    for (const k of skus.value) {
      out.skus[k] += r.skus?.[k] || 0
    }
  }
  out.avg_outlets_visit = out.active > 0 ? Number((out.outlets_visit / out.active).toFixed(1)) : 0
  return out
}

const regionTotalRow = computed(() => {
  if (!regionFilter.value && !search.value.trim() && payload.value?.regions_total) {
    return payload.value.regions_total
  }
  return summarize(filteredRegions.value, 'Grand Total')
})

const dealerTotalRow = computed(() => {
  if (!regionFilter.value && !dealerFilter.value && !search.value.trim() && payload.value?.dealers_total) {
    return payload.value.dealers_total
  }
  return summarize(filteredDealers.value, 'Grand Total')
})

const kpiSource = computed(() => (dealerFilter.value ? dealerTotalRow.value : regionTotalRow.value))
const totalTeam = computed(() => {
  const t = kpiSource.value
  return (t.sa || 0) + (t.sa_ws || 0) + (t.commando || 0) + (t.sa_leader || 0)
})
const totalSkuExecutions = computed(() =>
  skus.value.reduce((sum, k) => sum + (kpiSource.value.skus?.[k] || 0), 0),
)

const metricFields = ['sa', 'sa_ws', 'commando', 'sa_leader', 'al', 'active', 'district', 'commune', 'outlets_visit', 'avg_outlets_visit']

function computeMaxes(rows) {
  const maxes = {}
  for (const f of metricFields) {
    maxes[f] = Math.max(1, ...rows.map(r => Number(r[f] || 0)))
  }
  for (const k of skus.value) {
    maxes['sku:' + k] = Math.max(1, ...rows.map(r => Number(r.skus?.[k] || 0)))
  }
  return maxes
}

const regionMaxes = computed(() => computeMaxes(filteredRegions.value))
const dealerMaxes = computed(() => computeMaxes(filteredDealers.value))

function barWidth(value, maxVal) {
  const n = Number(value || 0)
  if (n <= 0 || !maxVal) return '0%'
  const pct = Math.min(100, Math.max(6, (n / maxVal) * 92))
  return `${pct.toFixed(1)}%`
}

function fmtAvg(value) {
  const n = Number(value || 0)
  return n === 0 ? '0' : n.toFixed(1)
}

let serial = 0
let timer = null

async function load() {
  const run = ++serial
  const f = fromDate.value
  const t = toDate.value
  if (!f || !t || f > t) {
    error.value = 'Choose a valid From Date and To Date.'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const data = await request('report-daily?' + new URLSearchParams({ from_date: f, to_date: t }))
    if (run !== serial) return
    const current = data.now.slice(0, 10)
    const followToday = f === today.value && t === today.value
    today.value = current
    if (followToday && t !== current) {
      fromDate.value = current
      toDate.value = current
      return
    }
    payload.value = data
  } catch (e) {
    if (run === serial) error.value = e.message || 'Failed to load daily report.'
  } finally {
    if (run === serial) loading.value = false
  }
}

watch([fromDate, toDate], () => {
  if (fromDate.value && toDate.value && fromDate.value > toDate.value) {
    fromDate.value = toDate.value
  }
  load()
})

function poll() {
  if (!document.hidden && toDate.value === today.value && !loading.value) {
    load()
  }
}

onMounted(() => {
  load()
  timer = setInterval(poll, 10000)
  document.addEventListener('visibilitychange', poll)
})

onUnmounted(() => {
  serial++
  clearInterval(timer)
  document.removeEventListener('visibilitychange', poll)
})

function exportExcel() {
  const qs = new URLSearchParams({ from_date: fromDate.value, to_date: toDate.value }).toString()
  window.open(`/api/report-daily/export?${qs}`, '_blank')
}

function exportCsv() {
  const dateText = fromDate.value === toDate.value ? fromDate.value : `${fromDate.value} to ${toDate.value}`
  const header = ['REGION', 'DEALER', 'SA', 'SA WS', 'Commando', 'SA Leader', 'AL', 'Active', 'District', 'Commune', '# Outlets Visit Based on Report', 'Average # of Outlets Visit', ...skus.value]
  const toRow = (r, withDealer) => [
    r.region,
    withDealer ? (r.dealer || '') : '',
    r.sa, r.sa_ws, r.commando, r.sa_leader, r.al, r.active,
    r.district, r.commune, r.outlets_visit, r.avg_outlets_visit,
    ...skus.value.map(k => r.skus?.[k] || 0),
  ]
  const lines = [
    ['Region: R1 to R8'],
    [`Date: ${dateText}`],
    [`#Dealer: ${payload.value?.dealer_count || 65}`],
    ['Daily Sales Report'],
    header,
    ...filteredRegions.value.map(r => toRow(r, false)),
    toRow(regionTotalRow.value, false),
    [],
    ['Daily Sales Report by Dealer'],
    header,
    ...filteredDealers.value.map(r => toRow(r, true)),
    toRow(dealerTotalRow.value, true),
  ]
  const csv = '\uFEFF' + lines.map(row => row.map(v => '"' + String(v ?? '').replaceAll('"', '""') + '"').join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `daily-sales-report-${fromDate.value}-to-${toDate.value}.csv`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <section class="report-daily-section">
    <div class="rd-filter-panel panel">
      <div class="rd-filter-row">
        <label class="rd-field">From Date
          <input type="date" v-model="fromDate" :max="toDate || today">
        </label>
        <label class="rd-field">To Date
          <input type="date" v-model="toDate" :min="fromDate" :max="today">
        </label>
        <label class="rd-field">Region
          <select v-model="regionFilter">
            <option value="">All regions (R1–R8)</option>
            <option v-for="r in allRegions" :key="r.region" :value="r.region">{{ r.region }}</option>
          </select>
        </label>
        <label class="rd-field">Dealer
          <select v-model="dealerFilter">
            <option value="">All dealers ({{ dealerOptions.length }})</option>
            <option v-for="d in dealerOptions" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="rd-field">Search
          <span class="rd-search-wrap">
            <Search :size="14" />
            <input type="search" v-model="search" placeholder="Region or dealer...">
          </span>
        </label>

        <div class="rd-actions">
          <div class="rd-tabs" role="tablist" aria-label="Report view">
            <button type="button" :class="{ active: viewMode === 'all' }" @click="viewMode = 'all'">All</button>
            <button type="button" :class="{ active: viewMode === 'region' }" @click="viewMode = 'region'">By Region</button>
            <button type="button" :class="{ active: viewMode === 'dealer' }" @click="viewMode = 'dealer'">By Dealer</button>
          </div>
          <span v-if="fromDate === today && toDate === today" class="map-live"><i />Live · 10s</span>
          <span v-else class="map-historical">Historical</span>
          <button type="button" class="icon" aria-label="Refresh daily report" :disabled="loading" @click="load()">
            <RefreshCw :size="15" :class="{ spin: loading }" />
          </button>
          <button type="button" class="btn" @click="exportCsv"><Download :size="14" />CSV</button>
          <button type="button" class="primary" @click="exportExcel"><FileSpreadsheet :size="14" />Export Excel</button>
        </div>
      </div>
    </div>

    <div class="rd-kpi-grid">
      <div class="rd-kpi">
        <span class="rd-kpi-label">Sales Team</span>
        <strong class="rd-kpi-value">{{ num(totalTeam) }}</strong>
        <span class="rd-kpi-sub">SA {{ num(kpiSource.sa) }} · WS {{ num(kpiSource.sa_ws) }} · CMD {{ num(kpiSource.commando) }} · TL {{ num(kpiSource.sa_leader) }}</span>
      </div>
      <div class="rd-kpi">
        <span class="rd-kpi-label">Active SA</span>
        <strong class="rd-kpi-value">{{ num(kpiSource.active) }} <small style="font-size:13px;color:#64748b">/ {{ num(totalTeam) }}</small></strong>
        <span class="rd-kpi-sub">{{ totalTeam ? Math.round((kpiSource.active / totalTeam) * 100) : 0 }}% checked in</span>
      </div>
      <div class="rd-kpi">
        <span class="rd-kpi-label">Annual Leave (AL)</span>
        <strong class="rd-kpi-value">{{ num(kpiSource.al) }}</strong>
        <span class="rd-kpi-sub">On approved leave</span>
      </div>
      <div class="rd-kpi">
        <span class="rd-kpi-label">Visit Coverage</span>
        <strong class="rd-kpi-value">{{ num(kpiSource.district) }} <small style="font-size:13px;color:#64748b">Dist</small></strong>
        <span class="rd-kpi-sub">{{ num(kpiSource.commune) }} Communes visited</span>
      </div>
      <div class="rd-kpi">
        <span class="rd-kpi-label"># Outlets Visit</span>
        <strong class="rd-kpi-value">{{ num(kpiSource.outlets_visit) }}</strong>
        <span class="rd-kpi-sub">Avg {{ fmtAvg(kpiSource.avg_outlets_visit) }} outlets / Active SA</span>
      </div>
      <div class="rd-kpi">
        <span class="rd-kpi-label">SKU Executions</span>
        <strong class="rd-kpi-value">{{ num(totalSkuExecutions) }}</strong>
        <span class="rd-kpi-sub">Across {{ skus.length }} SKUs · {{ payload?.dealer_count || 65 }} Dealers</span>
      </div>
    </div>

    <div v-if="error" class="error-banner" role="alert">{{ error }} <button @click="load()">Retry</button></div>

    <!-- Table 1: Daily Sales Report (By Region) -->
    <section v-if="viewMode === 'all' || viewMode === 'region'" class="panel rd-card">
      <div class="rd-card-head">
        <div class="rd-card-title">
          <h2>Daily Sales Report</h2>
          <div class="rd-card-meta">
            <span>Region: R1 to R8</span>
            <span>Date: {{ fromDate === toDate ? fromDate : `${fromDate} to ${toDate}` }}</span>
            <span>#Dealer: {{ payload?.dealer_count || 65 }}</span>
          </div>
        </div>
      </div>
      <div class="rd-table-wrap">
        <table class="rd-table">
          <thead>
            <tr class="rd-h1">
              <th rowspan="3" class="sticky-col-1">REGION</th>
              <th colspan="6" class="rd-group-border">Sales Team</th>
              <th colspan="2" class="rd-group-border">Location</th>
              <th rowspan="3" class="rd-group-border"># Outlets Visit<br>Based on Report</th>
              <th rowspan="3">Average #<br>of Outlets Visit</th>
              <th :colspan="skus.length" class="rd-group-border">Execution Purchase outlet By SKU</th>
            </tr>
            <tr class="rd-h2">
              <th colspan="6" class="rd-group-border">Team Status</th>
              <th colspan="2" class="rd-group-border">Actual Visit Location</th>
              <th :colspan="skus.length" class="rd-group-border">SKU Breakdown</th>
            </tr>
            <tr class="rd-h3">
              <th class="rd-group-border">SA</th>
              <th>SA WS</th>
              <th>Commando</th>
              <th>SA Leader</th>
              <th>AL</th>
              <th>Active</th>
              <th class="rd-group-border">District</th>
              <th>Commune</th>
              <th v-for="(sku, i) in skus" :key="sku" class="sku-col" :class="{ 'rd-group-border': i === 0 }">{{ sku }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filteredRegions" :key="r.region">
              <td class="sticky-col-1">{{ r.region }}</td>
              <td v-for="(f, fi) in ['sa','sa_ws','commando','sa_leader','al','active']" :key="f" class="rd-bar-cell" :class="{ 'rd-group-border': fi === 0 }">
                <span v-if="r[f] > 0" class="rd-bar-fill" :style="{ width: barWidth(r[f], regionMaxes[f]) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r[f] }">{{ num(r[f]) }}</span>
              </td>
              <td class="rd-bar-cell rd-group-border">
                <span v-if="r.district > 0" class="rd-bar-fill" :style="{ width: barWidth(r.district, regionMaxes.district) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.district }">{{ num(r.district) }}</span>
              </td>
              <td class="rd-bar-cell">
                <span v-if="r.commune > 0" class="rd-bar-fill" :style="{ width: barWidth(r.commune, regionMaxes.commune) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.commune }">{{ num(r.commune) }}</span>
              </td>
              <td class="rd-bar-cell rd-group-border">
                <span v-if="r.outlets_visit > 0" class="rd-bar-fill" :style="{ width: barWidth(r.outlets_visit, regionMaxes.outlets_visit) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.outlets_visit }">{{ num(r.outlets_visit) }}</span>
              </td>
              <td class="rd-bar-cell">
                <span v-if="r.avg_outlets_visit > 0" class="rd-bar-fill" :style="{ width: barWidth(r.avg_outlets_visit, regionMaxes.avg_outlets_visit) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.avg_outlets_visit }">{{ fmtAvg(r.avg_outlets_visit) }}</span>
              </td>
              <td v-for="(sku, si) in skus" :key="sku" class="rd-bar-cell" :class="{ 'rd-group-border': si === 0 }">
                <span v-if="(r.skus?.[sku] || 0) > 0" class="rd-bar-fill" :style="{ width: barWidth(r.skus?.[sku], regionMaxes['sku:' + sku]) }"></span>
                <span class="rd-bar-val" :class="{ zero: !(r.skus?.[sku] || 0) }">{{ num(r.skus?.[sku] || 0) }}</span>
              </td>
            </tr>
            <tr class="rd-total-row">
              <td class="sticky-col-1">Grand Total</td>
              <td class="rd-group-border">{{ num(regionTotalRow.sa) }}</td>
              <td>{{ num(regionTotalRow.sa_ws) }}</td>
              <td>{{ num(regionTotalRow.commando) }}</td>
              <td>{{ num(regionTotalRow.sa_leader) }}</td>
              <td>{{ num(regionTotalRow.al) }}</td>
              <td>{{ num(regionTotalRow.active) }}</td>
              <td class="rd-group-border">{{ num(regionTotalRow.district) }}</td>
              <td>{{ num(regionTotalRow.commune) }}</td>
              <td class="rd-group-border">{{ num(regionTotalRow.outlets_visit) }}</td>
              <td>{{ fmtAvg(regionTotalRow.avg_outlets_visit) }}</td>
              <td v-for="(sku, si) in skus" :key="sku" :class="{ 'rd-group-border': si === 0 }">{{ num(regionTotalRow.skus?.[sku] || 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Table 2: Daily Sales Report by Dealer -->
    <section v-if="viewMode === 'all' || viewMode === 'dealer'" class="panel rd-card">
      <div class="rd-card-head">
        <div class="rd-card-title">
          <h2>Daily Sales Report by Dealer</h2>
          <div class="rd-card-meta">
            <span>Showing {{ filteredDealers.length }} of {{ allDealers.length }} dealers</span>
            <span>Date: {{ fromDate === toDate ? fromDate : `${fromDate} to ${toDate}` }}</span>
          </div>
        </div>
      </div>
      <div class="rd-table-wrap">
        <table class="rd-table">
          <thead>
            <tr class="rd-h1">
              <th rowspan="3" class="sticky-col-1">REGION</th>
              <th rowspan="3" class="sticky-col-2">DEALER</th>
              <th colspan="6" class="rd-group-border">Sales Team</th>
              <th colspan="2" class="rd-group-border">Location</th>
              <th rowspan="3" class="rd-group-border"># Outlets Visit<br>Based on Report</th>
              <th rowspan="3">Average #<br>of Outlets Visit</th>
              <th :colspan="skus.length" class="rd-group-border">Execution Purchase outlet By SKU</th>
            </tr>
            <tr class="rd-h2">
              <th colspan="6" class="rd-group-border">Team Status</th>
              <th colspan="2" class="rd-group-border">Actual Visit Location</th>
              <th :colspan="skus.length" class="rd-group-border">SKU Breakdown</th>
            </tr>
            <tr class="rd-h3">
              <th class="rd-group-border">SA</th>
              <th>SA WS</th>
              <th>Commando</th>
              <th>SA Leader</th>
              <th>AL</th>
              <th>Active</th>
              <th class="rd-group-border">District</th>
              <th>Commune</th>
              <th v-for="(sku, i) in skus" :key="sku" class="sku-col" :class="{ 'rd-group-border': i === 0 }">{{ sku }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filteredDealers" :key="`${r.region}-${r.dealer}`">
              <td class="sticky-col-1">{{ r.region }}</td>
              <td class="sticky-col-2">{{ r.dealer }}</td>
              <td v-for="(f, fi) in ['sa','sa_ws','commando','sa_leader','al','active']" :key="f" class="rd-bar-cell" :class="{ 'rd-group-border': fi === 0 }">
                <span v-if="r[f] > 0" class="rd-bar-fill" :style="{ width: barWidth(r[f], dealerMaxes[f]) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r[f] }">{{ num(r[f]) }}</span>
              </td>
              <td class="rd-bar-cell rd-group-border">
                <span v-if="r.district > 0" class="rd-bar-fill" :style="{ width: barWidth(r.district, dealerMaxes.district) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.district }">{{ num(r.district) }}</span>
              </td>
              <td class="rd-bar-cell">
                <span v-if="r.commune > 0" class="rd-bar-fill" :style="{ width: barWidth(r.commune, dealerMaxes.commune) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.commune }">{{ num(r.commune) }}</span>
              </td>
              <td class="rd-bar-cell rd-group-border">
                <span v-if="r.outlets_visit > 0" class="rd-bar-fill" :style="{ width: barWidth(r.outlets_visit, dealerMaxes.outlets_visit) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.outlets_visit }">{{ num(r.outlets_visit) }}</span>
              </td>
              <td class="rd-bar-cell">
                <span v-if="r.avg_outlets_visit > 0" class="rd-bar-fill" :style="{ width: barWidth(r.avg_outlets_visit, dealerMaxes.avg_outlets_visit) }"></span>
                <span class="rd-bar-val" :class="{ zero: !r.avg_outlets_visit }">{{ fmtAvg(r.avg_outlets_visit) }}</span>
              </td>
              <td v-for="(sku, si) in skus" :key="sku" class="rd-bar-cell" :class="{ 'rd-group-border': si === 0 }">
                <span v-if="(r.skus?.[sku] || 0) > 0" class="rd-bar-fill" :style="{ width: barWidth(r.skus?.[sku], dealerMaxes['sku:' + sku]) }"></span>
                <span class="rd-bar-val" :class="{ zero: !(r.skus?.[sku] || 0) }">{{ num(r.skus?.[sku] || 0) }}</span>
              </td>
            </tr>
            <tr class="rd-total-row">
              <td class="sticky-col-1">Grand Total</td>
              <td class="sticky-col-2"></td>
              <td class="rd-group-border">{{ num(dealerTotalRow.sa) }}</td>
              <td>{{ num(dealerTotalRow.sa_ws) }}</td>
              <td>{{ num(dealerTotalRow.commando) }}</td>
              <td>{{ num(dealerTotalRow.sa_leader) }}</td>
              <td>{{ num(dealerTotalRow.al) }}</td>
              <td>{{ num(dealerTotalRow.active) }}</td>
              <td class="rd-group-border">{{ num(dealerTotalRow.district) }}</td>
              <td>{{ num(dealerTotalRow.commune) }}</td>
              <td class="rd-group-border">{{ num(dealerTotalRow.outlets_visit) }}</td>
              <td>{{ fmtAvg(dealerTotalRow.avg_outlets_visit) }}</td>
              <td v-for="(sku, si) in skus" :key="sku" :class="{ 'rd-group-border': si === 0 }">{{ num(dealerTotalRow.skus?.[sku] || 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
