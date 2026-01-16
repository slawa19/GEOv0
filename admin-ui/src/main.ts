import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'

import { createPinia } from 'pinia'
import { router } from './router'

import './style.css'
import App from './App.vue'

import { i18n } from './i18n'

createApp(App).use(createPinia()).use(router).use(ElementPlus).use(i18n).mount('#app')
