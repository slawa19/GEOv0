import type { Ref } from 'vue'
import { ref } from 'vue'
import { ensureSession } from '../api/simulatorApi'

export function useCookieSessionBootstrap(deps: {
  isRealMode: Readonly<Ref<boolean>>
  apiBase: Readonly<Ref<string>>
  accessToken: Readonly<Ref<string | null | undefined>>
}): {
  actorKind: Ref<string | null>
  ownerId: Ref<string | null>
  bootstrapping: Ref<boolean>
  tryEnsure: () => Promise<void>
} {
  const actorKind = ref<string | null>(null)
  const ownerId = ref<string | null>(null)
  const bootstrapping = ref(false)

  function getErrorMessage(error: unknown): string {
    if (error instanceof Error) return error.message
    return String(error)
  }

  async function tryEnsure(): Promise<void> {
    if (!deps.isRealMode.value) return
    if (String(deps.accessToken.value ?? '').trim()) return
    if (actorKind.value !== null) return
    if (bootstrapping.value) return

    bootstrapping.value = true
    try {
      const res = await ensureSession({ apiBase: deps.apiBase.value })
      actorKind.value = res.actor_kind
      ownerId.value = res.owner_id
    } catch (error: unknown) {
      console.warn('[GEO] Cookie session bootstrap failed:', getErrorMessage(error))
    } finally {
      bootstrapping.value = false
    }
  }

  return { actorKind, ownerId, bootstrapping, tryEnsure }
}
