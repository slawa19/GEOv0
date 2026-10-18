<script setup lang="ts">
import { useUiLocale } from '../composables/useUiLocale'
import { normalizeUiLocale, UI_LOCALE_ENDONYMS, UI_LOCALES } from '../i18n/locale'

/**
 * 046 wave 0 (T4601), decision 2: the language switch. It lives in the service row of the top bar, behind "Подробнее"
 * (not on the first glance: the 043 invariant of at most 7 stops holds; a visitor gets the browser's language). Each
 * option names its language in that language (`lang` on the option), the same in both interfaces.
 * A switch changes `<html lang>` and every text read through `t` without a reload (`i18n/locale.ts`).
 */
const { locale, setUiLocale, t } = useUiLocale()

function onChange(v: string) {
  const l = normalizeUiLocale(v)
  if (l) setUiLocale(l)
}
</script>

<template>
  <div class="ds-row lang-switch" data-testid="language-switch">
    <span class="ds-label">{{ t('locale.switcherLabel') }}</span>
    <select
      class="ds-select lang-switch__select"
      :value="locale"
      :aria-label="t('locale.switcherAria')"
      data-testid="language-select"
      @change="onChange(($event.target as HTMLSelectElement).value)"
    >
      <option v-for="l in UI_LOCALES" :key="l" :value="l" :lang="l">{{ UI_LOCALE_ENDONYMS[l] }}</option>
    </select>
  </div>
</template>

<style scoped>
/* The same measures as the theme row of the top bar (`TopBar.vue` `.tb-theme-row`, `.tb-select-28`). */
.lang-switch {
  gap: var(--ds-space-2, 8px);
  align-items: center;
}

.lang-switch__select {
  height: var(--ds-tb-control-h);
}

@media (max-width: 500px) {
  .lang-switch {
    flex-wrap: wrap;
  }
}
</style>
