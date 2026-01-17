import { describe, expect, it } from 'vitest'

import { setLocale, t } from './index'

describe('i18n graph stats punctuation', () => {
  it('does not include trailing colon in stats labels (EN)', () => {
    setLocale('en')
    expect(t('graph.stats.nodes')).not.toMatch(/:\s*$/)
    expect(t('graph.stats.edges')).not.toMatch(/:\s*$/)
    expect(t('graph.stats.bottlenecks')).not.toMatch(/:\s*$/)
  })

  it('does not include trailing colon in stats labels (RU)', () => {
    setLocale('ru')
    expect(t('graph.stats.nodes')).not.toMatch(/:\s*$/)
    expect(t('graph.stats.edges')).not.toMatch(/:\s*$/)
    expect(t('graph.stats.bottlenecks')).not.toMatch(/:\s*$/)
  })
})
