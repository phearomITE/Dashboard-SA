<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { dateLabel, num } from './workspace'
const props = defineProps({
  series: { type: Array, default: () => [] },
  lines: {
    type: Array,
    default: () => [
      { key: 'pins', label: 'Check In', color: '#127e72', fill: true },
      { key: 'alerts', label: 'Miss Report', color: '#d96b43' },
      { key: 'al', label: 'AL', color: '#6366f1', dashed: true },
    ],
  },
})
const selected = ref(null), svg = ref(null)
const width = ref(626), x0 = 42, x1 = computed(() => width.value - 24), y0 = 14, y1 = 170
let observer
onMounted(() => {
  observer = new ResizeObserver(([entry]) => { width.value = Math.max(260, entry.contentRect.width) })
  observer.observe(svg.value)
})
onUnmounted(() => observer?.disconnect())
const max = computed(() => {
  const vals = props.series.flatMap(d => props.lines.map(l => Number(d[l.key] || 0)))
  const v = Math.max(4, ...vals)
  const unit = 10 ** Math.floor(Math.log10(v / 4))
  return 4 * (Math.ceil(v / (4 * unit)) * unit)
})
const x = i => props.series.length <= 1
  ? (x0 + x1.value) / 2
  : x0 + (x1.value - x0) * i / (props.series.length - 1)
const y = v => y1 - (y1 - y0) * Number(v || 0) / max.value
function line(key) {
  let path = ''
  props.series.forEach((d, i) => {
    const val = Number(d[key] || 0)
    if (!i) { path = 'M ' + x(i) + ' ' + y(val); return }
    const prev = Number(props.series[i - 1][key] || 0)
    const mid = (x(i - 1) + x(i)) / 2
    path += ' C ' + mid + ' ' + y(prev) + ' ' + mid + ' ' + y(val) + ' ' + x(i) + ' ' + y(val)
  })
  return path
}
function bucketLabel(d) {
  return d.label || dateLabel(d.date, { month: 'short', day: 'numeric' })
}
const dates = computed(() => {
  const n = props.series.length
  if (!n) return []
  if (n <= 7) return props.series.map((d, i) => ({ d, i }))
  const count = width.value < 420 ? 4 : 7
  const indices = new Set(Array.from({ length: count }, (_, i) => Math.round(i * (n - 1) / (count - 1))))
  return props.series.map((d, i) => ({ d, i })).filter(({ i }) => indices.has(i))
})
function point(e) {
  if (!props.series.length) return
  const p = svg.value.createSVGPoint(); p.x = e.clientX; p.y = e.clientY
  const local = p.matrixTransform(svg.value.getScreenCTM().inverse())
  const idx = props.series.length <= 1 ? 0 : Math.round((local.x - x0) / (x1.value - x0) * (props.series.length - 1))
  selected.value = Math.max(0, Math.min(props.series.length - 1, idx))
}
function step(by) {
  if (!props.series.length) return
  selected.value = Math.max(0, Math.min(props.series.length - 1, (selected.value ?? 0) + by))
}
</script>
<template>
<div class="plot-wrap">
  <svg ref="svg" :viewBox="'0 0 '+width+' 198'" preserveAspectRatio="none" tabindex="0" role="img" aria-label="Trend line chart. Use left and right arrows to inspect."
    @pointermove="point" @pointerleave="selected=null" @focus="selected=0" @blur="selected=null" @keydown.left.prevent="step(-1)" @keydown.right.prevent="step(1)">
    <defs><linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#31a89b" stop-opacity=".20"/><stop offset="100%" stop-color="#36a99b" stop-opacity=".015"/></linearGradient></defs>
    <g v-for="n in 5" :key="n"><line :x1="x0" :x2="x1" :y1="y((n-1)*max/4)" :y2="y((n-1)*max/4)" stroke="#dde6e4" stroke-dasharray="3 5"/>
      <text x="34" :y="y((n-1)*max/4)+4" text-anchor="end">{{num((n-1)*max/4)}}</text></g>
    <template v-for="l in lines" :key="l.key">
      <path v-if="l.fill && series.length > 1" :d="line(l.key)+' L '+x(series.length-1)+' '+y1+' L '+x(0)+' '+y1+' Z'" fill="url(#trend-fill)"/>
      <path v-if="series.length > 1" :d="line(l.key)" class="chart-stroke" fill="none" :stroke="l.color" stroke-width="2.6" :stroke-dasharray="l.dashed ? '5 4' : null"/>
      <template v-if="series.length <= 31">
        <circle v-for="(d, i) in series" :key="l.key+'-pt-'+i" :cx="x(i)" :cy="y(d[l.key])" r="3.4" :fill="l.color" stroke="#fff" stroke-width="1.4"/>
      </template>
    </template>
    <text v-for="{d,i} in dates" :key="d.date+'-'+i" :x="x(i)" y="191" text-anchor="middle">{{bucketLabel(d)}}</text>
    <g v-if="selected!==null && series[selected]"><line :x1="x(selected)" :x2="x(selected)" :y1="y0" :y2="y1" stroke="#a9bfbb" stroke-dasharray="3 3"/>
      <circle v-for="l in lines" :key="l.key" :cx="x(selected)" :cy="y(series[selected][l.key])" r="4.8" :fill="l.color" stroke="white" stroke-width="2"/></g>
  </svg>
  <div v-if="selected!==null && series[selected]" class="chart-tip" :style="{left:Math.max(6,Math.min(71,x(selected)/width*100))+'%'}">
    <strong>{{bucketLabel(series[selected])}}</strong>
    <span v-for="l in lines" :key="l.key">{{l.label}}: {{num(series[selected][l.key]||0)}}</span>
  </div>
</div>
</template>
