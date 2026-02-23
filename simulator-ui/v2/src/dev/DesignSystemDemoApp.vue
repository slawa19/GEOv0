<script setup lang="ts">
import { computed, ref } from 'vue'
import type { UiThemeId } from '../types/uiPrefs'

const theme = ref<UiThemeId>('hud')
const density = ref<'comfortable' | 'compact'>('comfortable')
const reducedMotion = ref(false)

const rootAttrs = computed(() => ({
  'data-theme': theme.value,
  'data-density': density.value,
  'data-motion': reducedMotion.value ? 'reduced' : 'full',
}))

const progress = ref(72)

function bumpProgress() {
  progress.value = (progress.value + 11) % 101
}
</script>

<template>
  <div class="ds-page" v-bind="rootAttrs">
    <header class="ds-topbar">
      <div class="ds-topbar__title">
        <div class="ds-kicker">GEO Simulator UI</div>
        <h1 class="ds-h1">Design System Demo</h1>
        <p class="ds-help">
          Пример архитектуры: <span class="ds-code">tokens → primitives → composition</span>. Переключайте тему,
          плотность и motion — компоненты меняются через CSS-переменные.
        </p>
      </div>

      <div class="ds-controls ds-panel ds-panel--elevated">
        <div class="ds-controls__row">
          <label class="ds-label" for="theme">Theme</label>
          <select id="theme" v-model="theme" class="ds-select">
            <option value="shadcn">shadcn/ui (dark)</option>
            <option value="saas">SaaS (subtle)</option>
            <option value="library">Library (Naive-like)</option>
            <option value="hud">HUD (sci-fi)</option>
          </select>
        </div>

        <div class="ds-controls__row">
          <label class="ds-label" for="density">Density</label>
          <select id="density" v-model="density" class="ds-select">
            <option value="comfortable">Comfortable</option>
            <option value="compact">Compact</option>
          </select>
        </div>

        <div class="ds-controls__row ds-controls__row--inline">
          <label class="ds-switch">
            <input v-model="reducedMotion" type="checkbox" />
            <span class="ds-switch__ui" aria-hidden="true"></span>
            <span class="ds-label">Reduced motion</span>
          </label>
        </div>
      </div>
    </header>

    <main class="ds-grid">
      <!-- LEFT: primitive catalog -->
      <section class="ds-panel ds-panel--elevated">
        <header class="ds-panel__header">
          <h2 class="ds-h2">Primitives</h2>
          <p class="ds-help">Один набор классов — разные темы через tokens.</p>
        </header>

        <div class="ds-panel__body ds-stack">
          <div>
            <div class="ds-section-label">Buttons</div>
            <div class="ds-row">
              <button class="ds-btn ds-btn--primary">▶ Primary</button>
              <button class="ds-btn ds-btn--secondary">Secondary</button>
              <button class="ds-btn ds-btn--danger">✕ Danger</button>
              <button class="ds-btn ds-btn--ghost">Ghost</button>
              <button class="ds-btn ds-btn--icon" title="Refresh" aria-label="Refresh">⟳</button>
            </div>
          </div>

          <div>
            <div class="ds-section-label">Inputs</div>
            <div class="ds-two">
              <label class="ds-field">
                <span class="ds-label">Recipient</span>
                <input class="ds-input" placeholder="Enter participant name…" />
              </label>
              <label class="ds-field">
                <span class="ds-label">Amount</span>
                <input class="ds-input" type="number" placeholder="0.00" />
              </label>
            </div>
          </div>

          <div>
            <div class="ds-section-label">Select</div>
            <label class="ds-field">
              <span class="ds-label">Route</span>
              <select class="ds-select">
                <option>A → B</option>
                <option>B → C</option>
                <option>C → A</option>
              </select>
            </label>
          </div>

          <div>
            <div class="ds-section-label">Badges</div>
            <div class="ds-row">
              <span class="ds-badge ds-badge--ok">OK</span>
              <span class="ds-badge ds-badge--warn">Warning</span>
              <span class="ds-badge ds-badge--err">Error</span>
              <span class="ds-badge ds-badge--info">Info</span>
            </div>
          </div>

          <div>
            <div class="ds-section-label">Alerts</div>
            <div class="ds-stack">
              <div class="ds-alert ds-alert--ok"><span class="ds-alert__icon">✓</span> Payment processed successfully</div>
              <div class="ds-alert ds-alert--err"><span class="ds-alert__icon">✕</span> Insufficient capacity on route</div>
            </div>
          </div>

          <div>
            <div class="ds-section-label">Progress</div>
            <div class="ds-progress">
              <div class="ds-progress__label">
                <span>Clearing cycle</span>
                <span class="ds-code">{{ progress }}%</span>
              </div>
              <div class="ds-progress__track" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
                <div class="ds-progress__bar" :style="{ width: progress + '%' }"></div>
              </div>
              <div class="ds-row">
                <button class="ds-btn ds-btn--secondary" @click="bumpProgress">Change</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- RIGHT: composed screens -->
      <section class="ds-panel ds-panel--elevated">
        <header class="ds-panel__header">
          <h2 class="ds-h2">Composed UI</h2>
          <p class="ds-help">Пример «экранных» композиций из примитивов.</p>
        </header>

        <div class="ds-panel__body ds-stack">
          <div class="ds-subpanel">
            <div class="ds-subpanel__header">
              <div>
                <div class="ds-section-label">Manual Payment</div>
                <p class="ds-help">Создание ручного платежа между участниками сети GEO.</p>
              </div>
              <div class="ds-row">
                <span class="ds-badge ds-badge--info">Tx</span>
                <span class="ds-badge ds-badge--ok">Ready</span>
              </div>
            </div>

            <div class="ds-subpanel__body ds-stack">
              <div class="ds-two">
                <label class="ds-field">
                  <span class="ds-label">From</span>
                  <select class="ds-select">
                    <option>Alice</option>
                    <option>Bob</option>
                    <option>Carol</option>
                  </select>
                </label>
                <label class="ds-field">
                  <span class="ds-label">To</span>
                  <select class="ds-select">
                    <option>Bob</option>
                    <option>Carol</option>
                    <option>Alice</option>
                  </select>
                </label>
              </div>

              <label class="ds-field">
                <span class="ds-label">Amount</span>
                <input class="ds-input" type="number" placeholder="0.00" />
              </label>

              <div class="ds-row ds-row--space">
                <button class="ds-btn ds-btn--ghost">Advanced…</button>
                <div class="ds-row">
                  <button class="ds-btn ds-btn--secondary">Dry-run</button>
                  <button class="ds-btn ds-btn--primary">Send payment</button>
                </div>
              </div>
            </div>
          </div>

          <div class="ds-subpanel">
            <div class="ds-subpanel__header">
              <div class="ds-section-label">Node Card</div>
              <button class="ds-btn ds-btn--icon" aria-label="Close" title="Close">✕</button>
            </div>
            <div class="ds-node-card">
              <div class="ds-node-card__avatar">AL</div>
              <div class="ds-node-card__info">
                <div class="ds-node-card__name">Alice</div>
                <div class="ds-node-card__meta">Node • Active</div>
              </div>
              <div class="ds-node-card__balance">1,250.00</div>
            </div>
          </div>

          <div class="ds-subpanel">
            <div class="ds-subpanel__header">
              <div>
                <div class="ds-section-label">Node Details Card</div>
                <p class="ds-help">Шаблон: референсный Node Card + фактические поля Simulator UI.</p>
              </div>
              <div class="ds-row">
                <button class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button">Pin</button>
                <button class="ds-btn ds-btn--ghost ds-btn--icon" aria-label="Close" title="Close" type="button">✕</button>
              </div>
            </div>

            <div class="ds-subpanel__body ds-stack" style="gap: 12px">
              <div class="ds-node-card" role="group" aria-label="Node identity">
                <div class="ds-node-card__avatar">AL</div>
                <div class="ds-node-card__info">
                  <div class="ds-node-card__name">Alice</div>
                  <div class="ds-node-card__meta">Node • Active</div>
                </div>
                <div class="ds-node-card__balance">1,250.00</div>
              </div>

              <div class="ds-two" style="gap: 8px 12px">
                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">Type</span>
                  <span class="ds-value">Participant</span>
                </div>

                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">Status</span>
                  <span class="ds-value">Active</span>
                </div>

                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">Out</span>
                  <span class="ds-value ds-mono">13.00</span>
                  <span class="ds-label ds-muted">EUR</span>
                </div>

                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">In</span>
                  <span class="ds-value ds-mono">5.00</span>
                  <span class="ds-label ds-muted">EUR</span>
                </div>

                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">Net</span>
                  <span class="ds-value ds-mono">-1,250.00</span>
                </div>

                <div class="ds-row" style="gap: 6px; align-items: baseline">
                  <span class="ds-label">Degree</span>
                  <span class="ds-value ds-mono">3</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>

    <footer class="ds-footer">
      <p class="ds-help">
        Файлы: <span class="ds-code">designSystem.tokens.css</span> (tokens), <span class="ds-code">designSystem.primitives.css</span>
        (primitives), Vue-композиция в <span class="ds-code">DesignSystemDemoApp.vue</span>.
      </p>
    </footer>
  </div>
</template>




