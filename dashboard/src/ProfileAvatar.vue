<script setup>
import {computed,ref,watch} from 'vue'
import {UserRound} from 'lucide-vue-next'
const props=defineProps({telegramId:[String,Number],name:String})
const failed=ref(false)
watch(()=>props.telegramId,()=>{failed.value=false})
const source=computed(()=>props.telegramId && /^\d+$/.test(String(props.telegramId))
  ? '/api/profile-photo/'+encodeURIComponent(String(props.telegramId)) : '')
</script>
<template><span class="avatar profile-avatar" :aria-label="`${name||'SA'} profile photo`">
  <img v-if="source&&!failed" :src="source" :alt="`${name||'SA'} Telegram profile`" loading="lazy" @error="failed=true">
  <UserRound v-else :size="17" aria-hidden="true"/>
</span></template>
