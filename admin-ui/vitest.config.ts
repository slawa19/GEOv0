import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts'],
    // Vitest 3+ fakes every available timer API by default (requestAnimationFrame,
    // performance, ...). The suite was written against the Vitest 2 default below:
    // requestAnimationFrame stays the deterministic microtask stub from
    // src/test/setup.ts, and only timers + Date are faked.
    fakeTimers: {
      toFake: [
        'setTimeout',
        'clearTimeout',
        'setInterval',
        'clearInterval',
        'setImmediate',
        'clearImmediate',
        'Date',
      ],
    },
  },
})
