import { ref } from 'vue'

/**
 * Keeps an operator action from running twice for the same thing (032 S7, adversarial review): `run(key, action)`
 * starts the action unless one for that key is still running, and `has(key)` says so for the button (`:loading`).
 * The key is released when the action ends - success, refusal or failure - never earlier.
 */
export function useBusyKeys() {
  const keys = ref(new Set<string>())

  function has(key: string): boolean {
    return keys.value.has(key)
  }

  async function run(key: string, action: () => Promise<void>): Promise<void> {
    if (keys.value.has(key)) return
    keys.value = new Set(keys.value).add(key)
    try {
      await action()
    } finally {
      const next = new Set(keys.value)
      next.delete(key)
      keys.value = next
    }
  }

  return { has, run }
}
