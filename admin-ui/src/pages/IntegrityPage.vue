<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { describeError } from '../api/describeError'
import TooltipLabel from '../ui/TooltipLabel.vue'
import ListState from '../ui/ListState.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { promptReason } from '../ui/promptReason'
import { t } from '../i18n'
import type { IntegrityStatusResponse, IntegritySummaryResponse } from '../api/adminContracts'
import { useEquivalentPrecision } from '../composables/useEquivalentPrecision'
import { formatTs } from '../utils/datetime'
import { describeHoldClearRefusal } from './integrityHold'
import {
  detectedIssues as detectIssues,
  growthNotVerified,
  invariantOutcome,
  issueLabelKey,
  outcomeLabelKey,
  outcomeTagType,
  overLimitAllowed,
  violationsOf,
  type InvariantName,
} from './integrityOutcome'

const loading = ref(false)
const error = ref<string | null>(null)
const status = ref<IntegrityStatusResponse | null>(null)

const verifyLoading = ref(false)

type IntegrityStatus = IntegrityStatusResponse['status']
type EquivalentStatus = IntegrityStatusResponse['equivalents'][string]

function tagTypeForIntegrityStatus(s: IntegrityStatus): 'success' | 'warning' | 'danger' {
  if (s === 'healthy') return 'success'
  if (s === 'warning') return 'warning'
  return 'danger'
}

const overallStatus = computed<IntegrityStatus>(() => status.value?.status ?? 'warning')
const equivalents = computed(() => status.value?.equivalents ?? {})
const equivalentRows = computed(() => Object.entries(equivalents.value).map(([code, eq]) => ({ code, eq })))
const alertsCount = computed(() => status.value?.alerts.length ?? 0)

// Only a failed verdict is an issue: a check that is not verified, absent, or an allowed over-limit debt is not.
const detectedIssues = computed(() => detectIssues(equivalents.value))

// The money of an over-limit debt is printed at the precision of the equivalent of its row.
const { money, loadEquivalentPrecision } = useEquivalentPrecision()

const invariantColumns: ReadonlyArray<{ name: InvariantName; labelKey: string; minWidth: number }> = [
  { name: 'debt_symmetry', labelKey: 'integrity.columns.debtSymmetry', minWidth: 200 },
  { name: 'zero_sum', labelKey: 'integrity.columns.zeroSum', minWidth: 140 },
  { name: 'trust_limits', labelKey: 'integrity.columns.trustLimits', minWidth: 300 },
]

// One cell of the table: the outcome of one invariant of one equivalent, as decided by `integrityOutcome`.
function cell(eq: EquivalentStatus, name: InvariantName) {
  const entry = eq.invariants[name]
  const outcome = invariantOutcome(entry)
  return {
    tagType: outcomeTagType(outcome),
    label: t(outcomeLabelKey(outcome)),
    violations: violationsOf(entry),
    overLimit: overLimitAllowed(entry),
    growthNotVerified: growthNotVerified(entry),
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    status.value = await api.integrityStatus()
  } catch (e: unknown) {
    error.value = describeError(e).text
  } finally {
    loading.value = false
  }
}

async function verify() {
  try {
    await ElMessageBox.confirm(
      t('integrity.verify.confirmText'),
      t('integrity.verify.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.run'),
        cancelButtonText: t('common.cancel'),
      },
    )
  } catch {
    return
  }

  verifyLoading.value = true
  try {
    await api.integrityVerify()
    ElMessage.success(t('integrity.verify.finished'))
    await load()
  } catch (e: unknown) {
    ElMessage.error(describeError(e).text)
  } finally {
    verifyLoading.value = false
  }
}

// 032 S5 (F-4): the equivalents on an integrity hold, from `GET /integrity/summary` (`hold: true`), and the
// operator's action to lift one. Loaded apart from the status: a failing status check must not hide a hold.
type HoldRow = IntegritySummaryResponse['equivalents'][number]

const holdsLoading = ref(false)
const holdsError = ref<string | null>(null)
const holdRows = ref<HoldRow[]>([])
// Codes whose clear is in flight. Per code, not one slot: two prompts can be confirmed one after the other, and a
// single slot let the second overwrite the first (unblocking it) and the first's end release the second (032 S5 review).
// A code stays here until the summary has been read again, so its button cannot be pressed on a stale row.
const holdClearing = ref(new Set<string>())
const holdRefusal = ref<{ code: string; text: string } | null>(null)

const heldCount = computed(() => holdRows.value.filter((r) => r.hold).length)

async function loadHolds() {
  holdsLoading.value = true
  holdsError.value = null
  try {
    holdRows.value = (await api.integritySummary()).equivalents
  } catch (e: unknown) {
    holdsError.value = describeError(e).text
  } finally {
    holdsLoading.value = false
  }
}

async function clearHold(code: string) {
  const reason = await promptReason(
    t('integrity.holds.clearTitle', { code }),
    '',
    'integrity.holds.clear',
    'integrity.holds.reasonPlaceholder',
  )
  if (!reason) return

  // A prompt opened before this code's clear started can be confirmed while it is in flight: refuse the repeat.
  if (holdClearing.value.has(code)) return
  holdClearing.value = new Set(holdClearing.value).add(code)
  holdRefusal.value = null
  try {
    await api.clearIntegrityHold(code, reason)
    ElMessage.success(t('integrity.holds.cleared', { code }))
  } catch (e: unknown) {
    holdRefusal.value = { code, text: describeHoldClearRefusal(e) }
  }
  // The server's answer decides what is held, not the outcome of this click.
  try {
    await loadHolds()
  } finally {
    const next = new Set(holdClearing.value)
    next.delete(code)
    holdClearing.value = next
  }
}

async function loadCatalogue() {
  try {
    await loadEquivalentPrecision()
  } catch {
    // Not fatal and not hidden: without the catalogue the over-limit amounts print '—' (the project's rule for an
    // unknown precision) instead of a guessed digit count; the status itself does not depend on it.
  }
}

onMounted(() => {
  void load()
  void loadHolds()
  void loadCatalogue()
})
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('integrity.title')"
          tooltip-key="nav.integrity"
        />
        <el-button
          :loading="verifyLoading"
          type="primary"
          @click="verify"
        >
          {{ t('integrity.verify.action') }}
        </el-button>
      </div>
    </template>

    <div
      class="mb"
      data-testid="integrity-holds"
    >
      <div class="sub geoLabel">
        <TooltipLabel
          :label="t('integrity.holds.title')"
          :tooltip-text="t('integrity.holds.hint')"
        />
      </div>
      <LoadErrorAlert
        v-if="holdsError"
        :title="holdsError"
        :busy="holdsLoading"
        @retry="loadHolds"
      />
      <el-alert
        v-else-if="heldCount > 0"
        type="error"
        show-icon
        :closable="false"
        class="mb"
        :title="t('integrity.holds.heldSummary', { n: heldCount })"
        :description="t('integrity.holds.hint')"
      />
      <el-alert
        v-if="holdRefusal"
        type="warning"
        show-icon
        class="mb"
        data-testid="integrity-hold-refusal"
        :title="`${holdRefusal.code}: ${holdRefusal.text}`"
        @close="holdRefusal = null"
      />
      <div
        v-if="!holdsError"
        class="pillRow"
      >
        <div
          v-for="row in holdRows"
          :key="row.equivalent"
          class="holdItem"
          :data-testid="`integrity-hold-${row.equivalent}`"
        >
          <span class="mono">{{ row.equivalent }}</span>
          <el-tag
            :type="row.hold ? 'danger' : 'success'"
            effect="plain"
            size="small"
          >
            {{ row.hold ? t('integrity.holds.held') : t('integrity.holds.notHeld') }}
          </el-tag>
          <el-button
            v-if="row.hold"
            size="small"
            type="warning"
            data-testid="integrity-hold-clear"
            :loading="holdClearing.has(row.equivalent)"
            :disabled="holdClearing.has(row.equivalent)"
            @click="clearHold(row.equivalent)"
          >
            {{ t('integrity.holds.clear') }}
          </el-button>
        </div>
      </div>
    </div>

    <ListState
      :error="error"
      :loading="loading"
      :empty="!status"
      @retry="load"
    >
      <el-alert
        v-if="status"
        :type="overallStatus === 'critical' ? 'error' : overallStatus === 'warning' ? 'warning' : 'success'"
        show-icon
        :closable="false"
        class="mb"
      >
        <template #title>
          <span class="helpTitle">{{ t('integrity.help.title') }}</span>
        </template>

        <div class="help">
          <div
            v-if="overallStatus === 'healthy'"
            class="helpText"
          >
            {{ t('integrity.help.healthy') }}
          </div>

          <div
            v-else
            class="helpText"
          >
            {{ t('integrity.help.notHealthy') }}
          </div>

          <div
            v-if="detectedIssues.length"
            class="helpDetected"
          >
            <div class="helpHdr">
              {{ t('integrity.help.detectedIssues') }}
            </div>
            <div class="pillRow">
              <el-tag
                v-for="k in detectedIssues"
                :key="k"
                effect="plain"
                type="warning"
              >
                {{ t(issueLabelKey(k)) }}
              </el-tag>
            </div>
          </div>

          <div
            v-if="overallStatus !== 'healthy'"
            class="help"
          >
            <div class="helpHdr">
              {{ t('integrity.help.howToRespond') }}
            </div>
            <ul class="helpList">
              <li>{{ t('integrity.help.respond.stepVerify') }}</li>
              <li>{{ t('integrity.help.respond.stepAlerts') }}</li>
            </ul>
          </div>

          <el-divider class="helpDivider" />

          <div
            v-if="overallStatus !== 'healthy' && detectedIssues.length"
            class="helpHdr"
          >
            {{ t('integrity.help.interpretation') }}
          </div>

          <div
            v-if="detectedIssues.includes('debt_symmetry')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="warning">
                {{ t('common.warning') }}
              </el-tag>
              <span class="helpCaseName">{{ t('integrity.help.caseDebtSymmetry.title') }}</span>
            </div>
            <div class="helpText">
              {{ t('integrity.help.caseDebtSymmetry.text') }}
            </div>
            <ul class="helpList">
              <li>{{ t('integrity.help.caseDebtSymmetry.step1') }}</li>
            </ul>
          </div>

          <div
            v-if="detectedIssues.includes('trust_limits')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="danger">
                {{ t('common.critical') }}
              </el-tag>
              <span class="helpCaseName">{{ t('integrity.help.caseTrustLimits.title') }}</span>
            </div>
            <div class="helpText">
              {{ t('integrity.help.caseTrustLimits.text') }}
            </div>
          </div>

          <div
            v-if="detectedIssues.includes('zero_sum')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="danger">
                {{ t('common.critical') }}
              </el-tag>
              <span class="helpCaseName">{{ t('integrity.help.caseZeroSum.title') }}</span>
            </div>
            <div class="helpText">
              {{ t('integrity.help.caseZeroSum.text') }}
            </div>
            <ul class="helpList">
              <li>{{ t('integrity.help.caseZeroSum.step1') }}</li>
              <li>{{ t('integrity.help.caseZeroSum.step2') }}</li>
              <li>{{ t('integrity.help.caseZeroSum.step3') }}</li>
            </ul>
          </div>
        </div>
      </el-alert>

      <el-descriptions
        class="geoDescriptions"
        :column="2"
        border
      >
        <el-descriptions-item :label="t('common.status')">
          <el-tag :type="tagTypeForIntegrityStatus(overallStatus)">
            {{ t(`integrity.status.${overallStatus}`) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.lastCheck')">
          {{ formatTs(status?.last_check) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.alerts')">
          {{ alertsCount }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.equivalents')">
          {{ Object.keys(equivalents).length }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider />

      <div class="sub geoLabel">
        <TooltipLabel
          :label="t('integrity.section.equivalents')"
          :tooltip-text="t('integrity.help.equivalents')"
        />
      </div>
      <el-table
        :data="equivalentRows"
        size="small"
        border
        table-layout="fixed"
        class="tbl geoTable"
      >
        <el-table-column
          :label="t('common.code')"
          width="110"
        >
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('common.status')"
          width="120"
        >
          <template #default="{ row }">
            <el-tag :type="tagTypeForIntegrityStatus(row.eq.status)">
              {{ row.eq.status }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          v-for="col in invariantColumns"
          :key="col.name"
          :label="t(col.labelKey)"
          :min-width="col.minWidth"
        >
          <template #default="{ row }">
            <div class="row">
              <el-tag
                :type="cell(row.eq, col.name).tagType"
                effect="plain"
              >
                {{ cell(row.eq, col.name).label }}
              </el-tag>
              <span
                v-if="cell(row.eq, col.name).violations !== null"
                class="muted"
              >
                {{ t('common.violationsPrefix') }} {{ cell(row.eq, col.name).violations }}
              </span>
            </div>
            <!-- 026 T2601: reported, not failed - an allowed state and a check a snapshot cannot make. -->
            <div
              v-if="cell(row.eq, col.name).overLimit.length"
              class="overLimit"
              data-testid="integrity-over-limit-allowed"
            >
              <div class="muted">
                {{ t('integrity.overLimitAllowed', { n: cell(row.eq, col.name).overLimit.length }) }}
              </div>
              <ul class="helpList">
                <li
                  v-for="debt in cell(row.eq, col.name).overLimit"
                  :key="`${debt.debtor_id}|${debt.creditor_id}|${debt.equivalent_id}`"
                >
                  {{
                    t('integrity.overLimitItem', {
                      debtor: debt.debtor_id,
                      creditor: debt.creditor_id,
                      debt: money(debt.debt_amount, row.code),
                      limit: money(debt.trust_limit, row.code),
                      excess: money(debt.excess, row.code),
                    })
                  }}
                </li>
              </ul>
            </div>
            <div
              v-if="cell(row.eq, col.name).growthNotVerified"
              class="muted"
              data-testid="integrity-growth-not-verified"
            >
              {{ t('integrity.growthNotVerified') }}
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-divider />

      <div class="sub geoLabel">
        {{ t('integrity.section.rawPayload') }}
      </div>
      <pre class="json">{{ JSON.stringify(status, null, 2) }}</pre>
    </ListState>
  </el-card>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb {
  margin-bottom: 12px;
}
.sub {
  margin-bottom: 6px;
}
.helpTitle {
  font-weight: 600;
}
.help {
  line-height: 1.35;
}
.helpDetected {
  margin-top: 6px;
}
.pillRow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.helpDivider {
  margin: 10px 0;
}
.helpCase {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 10px;
  margin-top: 10px;
}
.helpCaseTitle {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.helpCaseName {
  font-weight: 600;
}
.helpHdr {
  font-size: var(--geo-font-size-title);
  font-weight: var(--geo-font-weight-title);
  margin-top: 6px;
  margin-bottom: 4px;
}
.helpText {
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-primary);
}
.helpList {
  margin: 0;
  padding-left: 18px;
  font-size: var(--geo-font-size-label);
}
.helpList li {
  margin: 3px 0;
}
.helpNote {
  margin-top: 8px;
  font-size: var(--geo-font-size-sub);
  color: var(--el-text-color-secondary);
}
.holdItem {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 4px 8px;
}
.overLimit {
  margin-top: 4px;
}
.tbl {
  width: 100%;
  margin-bottom: 8px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.muted {
  font-size: var(--geo-font-size-sub);
  color: var(--el-text-color-secondary);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
.json {
  margin: 0;
  font-size: var(--geo-font-size-sub);
}
</style>
