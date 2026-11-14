<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { dateLabel, num } from './workspace'
const props = defineProps({ series:Array })
const selected=ref(null), svg=ref(null)
const width=ref(626), x0=38, x1=computed(()=>width.value-22), y0=14, y1=170
let observer
onMounted(()=>{
  observer=new ResizeObserver(([entry])=>{width.value=Math.max(260,entry.contentRect.width)})
  observer.observe(svg.value)
})
onUnmounted(()=>observer?.disconnect())
const max=computed(()=>{
  const v=Math.max(4,...props.series.flatMap(d=>[d.reports,d.pins]))
  const unit=10**Math.floor(Math.log10(v/4))
  return 4 * (Math.ceil(v / (4*unit))*unit)
})
const x=i=>x0+(x1.value-x0)*i/Math.max(1,props.series.length-1)
const y=v=>y1-(y1-y0)*v/max.value
// A horizontal tangent at each observation keeps the curve within adjacent values.
function line(key){
  let path=''
  props.series.forEach((d,i)=>{
    if(!i) { path='M '+x(i)+' '+y(d[key]); return }
    const mid=(x(i-1)+x(i))/2
    path+=' C '+mid+' '+y(props.series[i-1][key])+' '+mid+' '+y(d[key])+' '+x(i)+' '+y(d[key])
  })
  return path
}
const dates=computed(()=>{
  const count=width.value<400?4:7
  const indices=new Set(Array.from({length:count},(_,i)=>Math.round(i*(props.series.length-1)/(count-1))))
  return props.series.map((d,i)=>({d,i})).filter(({i})=>indices.has(i))
})
function point(e){
  const p=svg.value.createSVGPoint(); p.x=e.clientX; p.y=e.clientY
  const local=p.matrixTransform(svg.value.getScreenCTM().inverse())
  selected.value=Math.max(0,Math.min(props.series.length-1,Math.round((local.x-x0)/(x1.value-x0)*(props.series.length-1))))
}
function step(by){selected.value=Math.max(0,Math.min(props.series.length-1,(selected.value??0)+by))}
</script>
<template>
<div class="plot-wrap">
  <svg ref="svg" :viewBox="'0 0 '+width+' 198'" preserveAspectRatio="none" tabindex="0" role="img" aria-label="Daily sale reports and location pins. Use left and right arrows to inspect."
    @pointermove="point" @pointerleave="selected=null" @focus="selected=0" @blur="selected=null" @keydown.left.prevent="step(-1)" @keydown.right.prevent="step(1)">
    <defs><linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#31a89b" stop-opacity=".23"/><stop offset="100%" stop-color="#36a99b" stop-opacity=".018"/></linearGradient></defs>
    <g v-for="n in 5" :key="n"><line :x1="x0" :x2="x1" :y1="y((n-1)*max/4)" :y2="y((n-1)*max/4)" stroke="#dde6e4" stroke-dasharray="3 5"/>
      <text x="27" :y="y((n-1)*max/4)+4" text-anchor="end">{{num((n-1)*max/4)}}</text></g>
    <path :d="line('reports')+' L '+x1+' '+y1+' L '+x0+' '+y1+' Z'" fill="url(#trend-fill)"/>
    <path :d="line('reports')" class="chart-stroke" fill="none" stroke="#127e72" stroke-width="3"/>
    <path :d="line('pins')" class="chart-stroke" fill="none" stroke="#e0a265" stroke-width="2.4" stroke-dasharray="5 5"/>
    <text v-for="{d,i} in dates" :key="d.date" :x="x(i)" y="191" text-anchor="middle">{{dateLabel(d.date,{month:'short',day:'numeric'})}}</text>
    <g v-if="selected!==null && series[selected]"><line :x1="x(selected)" :x2="x(selected)" :y1="y0" :y2="y1" stroke="#a9bfbb" stroke-dasharray="3 3"/>
      <circle v-for="key in ['reports','pins']" :key="key" :cx="x(selected)" :cy="y(series[selected][key])" r="4" :fill="key==='pins'?'#e0a265':'#127e72'" stroke="white" stroke-width="2"/></g>
  </svg>
  <div v-if="selected!==null && series[selected]" class="chart-tip" :style="{left:Math.max(6,Math.min(71,x(selected)/width*100))+'%'}">
    <strong>{{dateLabel(series[selected].date,{month:'short',day:'numeric'})}}</strong><span>{{series[selected].reports}} reports · {{series[selected].pins}} pins</span></div>
</div>
</template>
