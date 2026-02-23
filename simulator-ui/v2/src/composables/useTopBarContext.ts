import { inject, provide, type InjectionKey, type Ref } from 'vue'
import type { RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'
import type { AdminRunSummary } from '../api/simulatorApi'
import type { UiThemeId } from '../types/uiPrefs'

export type TopBarContext = {
  // mode + env
  apiMode: Readonly<Ref<'fixtures' | 'real'>>
  activeSegment: Readonly<Ref<'sandbox' | 'auto' | 'interact'>>
  isInteractUi: Readonly<Ref<boolean>>
  /** True when UI runs in automation/deterministic mode (VITE_TEST_MODE=1). */
  isTestMode: Readonly<Ref<boolean | undefined>>

  // UI prefs
  uiTheme: Readonly<Ref<UiThemeId>>

  // scenarios / run controls
  loadingScenarios: Readonly<Ref<boolean>>
  scenarios: Readonly<Ref<ScenarioSummary[]>>
  selectedScenarioId: Readonly<Ref<string>>
  desiredMode: Readonly<Ref<SimulatorMode>>
  intensityPercent: Readonly<Ref<number>>

  // run status
  runId: Readonly<Ref<string | null>>
  runStatus: Readonly<Ref<RunStatus | null>>
  sseState: Readonly<Ref<string>>
  lastError: Readonly<Ref<string | null>>

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

  // admin
  /** Current access token — used to determine if admin controls should be shown. */
  accessToken: Readonly<Ref<string | null | undefined>>
  /** Admin: list of all runs (populated after adminGetRuns). */
  adminRuns: Readonly<Ref<AdminRunSummary[] | null | undefined>>
  /** Admin: true while loading all runs. */
  adminRunsLoading: Readonly<Ref<boolean | undefined>>
  /** Admin: last error from admin actions. */
  adminLastError: Readonly<Ref<string | undefined>>

  // admin capabilities (mirror previous optional callback-props existence)
  adminCanGetRuns: Readonly<Ref<boolean>>
  adminCanStopRuns: Readonly<Ref<boolean>>
  adminCanAttachRun: Readonly<Ref<boolean>>
  adminCanStopRun: Readonly<Ref<boolean>>
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

