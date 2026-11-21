<script setup>
import {onMounted,onUnmounted,ref,watch} from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import {Crosshair,Layers3,Minus,Plus} from 'lucide-vue-next'
import {useWorkspace} from './workspace'
import brandLogo from './assets/chip-mong-foodbev.png'

const props=defineProps({events:{type:Array,required:true},origins:{type:Array,required:true},roadRoutes:{type:Object,default:()=>({})},selected:String,fitKey:String,focused:Number})
const emit=defineEmits(['select','focus'])
const {request}=useWorkspace()
const element=ref(null),mapType=ref('street'),areaError=ref(false),tileError=ref(false),showDealers=ref(true)
let map,geometry,labels,points,dealersLayer,road,satellite,observer,revision=0,viewportKey='',countryAreas
const cambodia=[[10.35,102.32],[14.69,107.64]]
const inCambodia=p=>Number.isFinite(Number(p.lat))&&Number.isFinite(Number(p.lon))&&p.lat>=10.2&&p.lat<=14.8&&p.lon>=102.2&&p.lon<=107.8
const fills=['#e2eadb','#dde9dd','#e7eddc','#d7e5d9','#e8ecdf','#e1e8de','#e0e9d5','#e3e8d6']
function pinIcon(value,origin=false,active=false){
  const badge=document.createElement('span')
  badge.className='map-stop'+(origin?' depot':'')+(active?' focused':'')
  badge.textContent=String(value)
  return L.divIcon({className:'route-marker',html:badge,iconSize:[28,28],iconAnchor:[14,14]})
}
function plainLabel(text){const el=document.createElement('span');el.textContent=text;return el}
function formatDistanceKm(kmValue){
  if(kmValue==null||!Number.isFinite(Number(kmValue)))return ''
  return Number(kmValue).toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:2})+' km'
}
function dealerIcon(){
  const image=document.createElement('img');image.src=brandLogo;image.alt='Dealer location'
  const badge=document.createElement('span');badge.className='dealer-logo-pin';badge.append(image)
  return L.divIcon({className:'dealer-logo-marker',html:badge,iconSize:[34,34],iconAnchor:[17,17]})
}
function repaint(){
  if(!map)return
  points.clearLayers();dealersLayer.clearLayers()
  for(const origin of props.origins){
    if(!inCambodia(origin))continue
    const coord=[origin.lat,origin.lon]
    if(showDealers.value)L.marker(coord,{icon:dealerIcon(),title:'Dealer '+origin.dealer})
      .bindTooltip(plainLabel('Dealer · '+origin.region+' / '+origin.dealer)).addTo(dealersLayer)
  }
  if(props.selected && props.roadRoutes){
    for(const rr of Object.values(props.roadRoutes)){
      if(rr&&rr.available&&Array.isArray(rr.coordinates)&&rr.coordinates.length>=2){
        L.polyline(rr.coordinates,{color:'#17715a',weight:3.5,opacity:.92,interactive:false}).addTo(points)
      }
    }
  }
  for(const p of props.events){
    if(!inCambodia(p))continue
    const coord=[p.lat,p.lon]
    const key=JSON.stringify([p.region,p.dealer])
    const rr=props.roadRoutes?.[key]
    const roadKm=rr&&rr.available&&rr.legByEventId?.[p.id]!=null?Number(rr.legByEventId[p.id]):null
    const distText=formatDistanceKm(roadKm)
    const distLabel=distText?' · Road Distance: '+distText:''
    const tooltipLabel=p.name+' · Outlet '+p.outlet+' · '+p.at.slice(11,16)+distLabel
    const marker=props.selected
      ? L.marker(coord,{icon:pinIcon(p.outlet,false,props.focused===p.id),title:tooltipLabel,zIndexOffset:props.focused===p.id?1000:0})
      : L.circleMarker(coord,{radius:6,color:'#fff',weight:2,fillColor:'#167758',fillOpacity:.94})
    marker.bindTooltip(plainLabel(tooltipLabel))
      .on('click',()=>{emit('select',p.employee_id);emit('focus',p.id)}).addTo(points)
  }
}
function fit(){
  if(!map)return
  map.invalidateSize()
  const coords=props.selected?[...props.events,...props.origins].filter(inCambodia).map(p=>[p.lat,p.lon]):[]
  if(!coords.length){map.fitBounds(cambodia,{animate:false});return}
  map.fitBounds(L.latLngBounds(coords),{padding:[42,42],maxZoom:15,animate:false})
}
async function areas(){
  if(!map||mapType.value!=='area')return
  const bounds=map.getBounds(), zoom=map.getZoom()
  const box=bounds.pad(.12)
  const query=zoom>=10?new URLSearchParams({west:Math.max(-180,box.getWest()),east:Math.min(180,box.getEast()),south:box.getSouth(),north:box.getNorth(),detail:true}).toString():''
  const key=zoom+':'+bounds.toBBoxString()
  if(key===viewportKey && !areaError.value)return
  viewportKey=key
  const run=++revision
  try{
    const data=query?await request('live-map/areas?'+query):(countryAreas||=await request('live-map/areas'))
    if(run!==revision || !map || mapType.value!=='area')return
    geometry.clearLayers();labels.clearLayers();areaError.value=false
    L.geoJSON(data,{interactive:false,pane:'boundaries',style:f=>({color:'#fff',weight:1.2,opacity:.92,fillOpacity:1,fillColor:fills[Number(f.properties.code.slice(2))%fills.length]})}).addTo(geometry)
    const placed=[]
    for(const feature of data.features){
      const {center,name}=feature.properties
      const coord=[center[1],center[0]], pixel=map.latLngToContainerPoint(coord)
      if(!bounds.contains(coord)||placed.some(p=>Math.abs(p.x-pixel.x)<95 && Math.abs(p.y-pixel.y)<27))continue
      placed.push(pixel)
      L.marker(coord,{pane:'boundaryLabels',interactive:false,keyboard:false,icon:L.divIcon({className:'map-area-name',html:plainLabel(name),iconSize:[110,22],iconAnchor:[55,11]})}).addTo(labels)
    }
  }catch{if(run===revision)areaError.value=true}
}
function basemap(type){
  mapType.value=type;tileError.value=false
  map.removeLayer(geometry);map.removeLayer(labels);map.removeLayer(road);map.removeLayer(satellite)
  if(type==='street')road.addTo(map)
  else if(type==='satellite')satellite.addTo(map)
  else{geometry.addTo(map);labels.addTo(map);viewportKey='';areas()}
}
onMounted(()=>{
  map=L.map(element.value,{zoomControl:false,minZoom:5,maxZoom:19,scrollWheelZoom:true,preferCanvas:true,attributionControl:false})
  map.createPane('boundaries').style.zIndex=210
  map.createPane('boundaryLabels').style.zIndex=230
  geometry=L.layerGroup();labels=L.layerGroup();points=L.layerGroup().addTo(map);dealersLayer=L.layerGroup().addTo(map)
  road=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin'})
  road.on('tileerror',()=>{tileError.value=true})
  satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin'})
  satellite.on('tileerror',()=>{tileError.value=true})
  road.addTo(map);fit();map.on('moveend',areas);repaint()
  observer=new ResizeObserver(()=>{map?.invalidateSize();if(map&&!props.selected)fit()});observer.observe(element.value)
})
watch(()=>[props.events,props.origins,props.roadRoutes,props.selected,props.focused],repaint)
watch(showDealers,repaint)
watch(()=>props.fitKey,fit,{flush:'post'})
watch(()=>props.focused,id=>{const p=props.events.find(r=>r.id===id);if(p && map)map.setView([p.lat,p.lon],Math.max(map.getZoom(),14),{animate:false})})
onUnmounted(()=>{revision++;observer?.disconnect();map?.remove();map=null})
</script>
<template><div class="field-map">
  <div ref="element" class="map-canvas" role="region" aria-label="Interactive employee check-in map"></div>
  <div class="map-style-control" role="group" aria-label="Map type"><Layers3 :size="15"/><button v-for="type in ['street','satellite','area']" :key="type" :aria-pressed="mapType===type" :class="{active:mapType===type}" @click="basemap(type)">{{type==='area'?'Areas':type[0].toUpperCase()+type.slice(1)}}</button><label><input type="checkbox" v-model="showDealers">Dealers</label></div>
  <div class="map-controls"><button aria-label="Zoom in" @click="map.zoomIn()"><Plus/></button><button aria-label="Zoom out" @click="map.zoomOut()"><Minus/></button><button aria-label="Fit visible locations" @click="fit"><Crosshair/></button></div>
  <div v-if="areaError&&mapType==='area'" class="map-error"><button @click="areas">Reload area boundaries</button></div>
  <div v-if="mapType!=='area'&&tileError" class="map-error"><button @click="basemap('area')">Map tiles unavailable · Show areas</button></div>
  <div class="map-credit"><a v-if="mapType==='street'" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a><a v-else-if="mapType==='satellite'" href="https://www.esri.com/" target="_blank" rel="noreferrer">Esri World Imagery</a><a v-else href="https://data.humdata.org/dataset/cod-ab-khm" target="_blank" rel="noreferrer">Cambodia DoG · OCHA, 2018</a></div>
</div></template>
