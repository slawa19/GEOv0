import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'

import { createPinia } from 'pinia'
import { router } from './router'

import './style.css'
import App from './App.vue'

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
