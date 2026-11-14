import { createApp } from 'vue'
import '@fontsource-variable/dm-sans/wght.css'
import '@fontsource/kantumruy-pro/khmer-400.css'
import '@fontsource/kantumruy-pro/khmer-600.css'
import './style.css'
import App from './App.vue'
import { createWorkspace, workspaceKey } from './workspace'

const app = createApp(App)
app.provide(workspaceKey, createWorkspace())
app.mount('#app')
