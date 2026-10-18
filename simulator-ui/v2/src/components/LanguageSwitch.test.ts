/**
 * 046 wave 0 (T4601), verification plan 1(c) at the component level: choosing a language in the switch changes
 * `<html lang>`, the switch's own text and any other text read through `t` - without a reload and without touching the
 * state around it. LIMITS: the switch is not yet wired into the top bar (after 044 stage 2); that it sits behind
 * "Подробнее", and that a running flow survives a switch in the real app, are judged when it is wired.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'

import { setUiLocale, UI_LOCALE_STORAGE_KEY } from '../i18n/locale'
import { t } from '../i18n/t'
import LanguageSwitch from './LanguageSwitch.vue'

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
})

async function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const counter = ref(0)
  // A neighbour with its own state and a text of the dictionary: the state must survive, the text must follow.
  const Neighbour = defineComponent({
    setup: () => () => h('p', { 'data-testid': 'neighbour' }, `${t('locale.switcherAria')} #${counter.value}`),
  })
  const app = createApp({ setup: () => () => h('div', [h(LanguageSwitch), h(Neighbour)]) })
  app.mount(host)
  await nextTick()
  return { host, app, counter }
}

function choose(host: HTMLElement, value: string) {
  const select = host.querySelector<HTMLSelectElement>('[data-testid="language-select"]')!
  select.value = value
  select.dispatchEvent(new Event('change'))
}

describe('046 T4601 - the language switch', () => {
  it('offers each language named in itself, the current one selected', async () => {
    setUiLocale('en')
    const { host, app } = await mount()
    const options = [...host.querySelectorAll('option')].map((o) => [o.value, o.getAttribute('lang'), o.textContent])
    expect(options).toEqual([['en', 'en', 'English'], ['ru', 'ru', 'Русский']])
    expect(host.querySelector<HTMLSelectElement>('select')!.value).toBe('en')
    expect(host.querySelector('select')!.getAttribute('aria-label')).toBe('Interface language')
    app.unmount(); host.remove()
  })

  it('choosing ru: <html lang>, the label, the aria-label and a neighbour change; no reload; the neighbour keeps its state', async () => {
    setUiLocale('en')
    ;(window as unknown as { __sameDocument?: number }).__sameDocument = 1
    const href = window.location.href
    const { host, app, counter } = await mount()
    counter.value = 3
    await nextTick()
    expect(host.querySelector('[data-testid="neighbour"]')!.textContent).toBe('Interface language #3')

    choose(host, 'ru')
    await nextTick()

    expect(document.documentElement.lang).toBe('ru')
    expect(host.querySelector('[data-testid="language-switch"] .ds-label')!.textContent).toBe('Язык')
    expect(host.querySelector('select')!.getAttribute('aria-label')).toBe('Язык интерфейса')
    expect(host.querySelector('[data-testid="neighbour"]')!.textContent, 'the text followed, the state stayed').toBe('Язык интерфейса #3')
    expect(window.localStorage.getItem(UI_LOCALE_STORAGE_KEY)).toBe('ru')
    expect(window.location.href, 'no navigation').toBe(href)
    expect((window as unknown as { __sameDocument?: number }).__sameDocument, 'the same document').toBe(1)

    choose(host, 'en')
    await nextTick()
    expect([document.documentElement.lang, host.querySelector('[data-testid="language-switch"] .ds-label')!.textContent]).toEqual(['en', 'Language'])
    app.unmount(); host.remove()
  })

  it('a value the switch does not offer changes nothing', async () => {
    setUiLocale('ru')
    window.localStorage.clear()
    const { host, app } = await mount()
    const select = host.querySelector<HTMLSelectElement>('select')!
    const option = document.createElement('option')
    option.value = 'de'
    select.appendChild(option)
    choose(host, 'de')
    await nextTick()
    expect([document.documentElement.lang, window.localStorage.getItem(UI_LOCALE_STORAGE_KEY)]).toEqual(['ru', null])
    app.unmount(); host.remove()
  })
})
