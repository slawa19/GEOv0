import { inject, provide, type InjectionKey, type Ref } from 'vue'
import type { RunStatus, ScenarioSummary } from '../api/simulatorTypes'
import type { UiThemeId } from '../types/uiPrefs'
import { isJwtLike } from '../utils/isJwtLike'

/**
 * Who sees the administrator's controls: an admin token - not a participant JWT - on the server source. 048 S2: the row
 * of the own run and the payments badge of the top bar (amendment 8); "Admin ▾" is gone and the analytics dock is the
 * developer's (`?debug=1`), so the top bar is the one reader.
 */
export function isAdminViewer(accessToken: string | null | undefined, apiMode: 'fixtures' | 'real'): boolean {
  const t = String(accessToken ?? '').trim()
  return !!t && !isJwtLike(t) && apiMode === 'real'
}

export type TopBarContext = {
  // mode + env
  /** 043 S2: the one screen has no mode segment; the source (server or offline snapshot) is all that differs. */
  apiMode: Readonly<Ref<'fixtures' | 'real'>>
  /** True when UI runs in automation/deterministic mode (VITE_TEST_MODE=1). */
  isTestMode: Readonly<Ref<boolean | undefined>>

  // UI prefs
  uiTheme: Readonly<Ref<UiThemeId>>

  // scenarios / run controls
  loadingScenarios: Readonly<Ref<boolean>>
  scenarios: Readonly<Ref<ScenarioSummary[]>>
  selectedScenarioId: Readonly<Ref<string>>
  intensityPercent: Readonly<Ref<number>>

  // run status
  runId: Readonly<Ref<string | null>>
  runStatus: Readonly<Ref<RunStatus | null>>
  sseState: Readonly<Ref<string>>
  lastError: Readonly<Ref<string | null>>
  /**
   * 046 wave 1: the error line is a start the global limit of runs refused, and may be tried again ("Try again" beside
   * it). Optional: a context without it offers no retry.
   */
  startRetryable?: Readonly<Ref<boolean>>

  runStats: Readonly<
    Ref<{
      startedAtMs: number
      attempts: number
      committed: number
      rejected: number
      errors: number
      timeouts: number
      rejectedByCode: Record<string, number>
      errorsByCode: Record<string, number>
    }>
  >

  /** The current access token: decides whether the administrator's controls are shown (`isAdminViewer`). */
  accessToken: Readonly<Ref<string | null | undefined>>
}

export const TOP_BAR_CONTEXT: InjectionKey<TopBarContext> = Symbol('TopBarContext')

export function provideTopBarContext(ctx: TopBarContext): void {
  provide(TOP_BAR_CONTEXT, ctx)
}

export function useTopBarContext(): TopBarContext {
  const ctx = inject(TOP_BAR_CONTEXT, null)
  if (!ctx) {
    throw new Error(
      '[TopBar] TopBarContext is not provided. ' +
        'Call provideTopBarContext() in SimulatorAppRoot (or an ancestor) before rendering <TopBar />.'
    )
  }
  return ctx
}

