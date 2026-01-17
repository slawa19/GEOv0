import { computed } from 'vue'

import { t } from '../../i18n'

export type Option = { label: string; value: string }

export function useGraphPageOptions() {
  const statuses = computed<Option[]>(() => [
    { label: t('trustlines.status.active'), value: 'active' },
    { label: t('trustlines.status.frozen'), value: 'frozen' },
    { label: t('trustlines.status.closed'), value: 'closed' },
  ])

  const layoutOptions = computed<Option[]>(() => [
    { label: t('graph.display.layoutOption.fcose'), value: 'fcose' },
    { label: t('graph.display.layoutOption.grid'), value: 'grid' },
    { label: t('graph.display.layoutOption.circle'), value: 'circle' },
  ])

  return { statuses, layoutOptions }
}
