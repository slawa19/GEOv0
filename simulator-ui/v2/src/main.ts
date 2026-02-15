import { createApp } from 'vue'
import App from './App.vue'

// Design system (tokens → primitives → composition)
import './ui-kit/designSystem.tokens.css'
import './ui-kit/designSystem.primitives.css'
import './ui-kit/designSystem.overlays.css'

// Global base
import './styles.css'

createApp(App).mount('#app')
