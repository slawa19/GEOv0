import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'

import { createPinia } from 'pinia'
import { router } from './router'

import './style.css'
import App from './App.vue'

// Dark mode is allowed by spec; can be toggled in the UI.
document.documentElement.classList.add('dark')

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
