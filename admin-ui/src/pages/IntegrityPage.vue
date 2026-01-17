<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { formatApiError } from '../api/errorFormat'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { t } from '../i18n'

const authStore = useAuthStore()

const loading = ref(false)
const error = ref<string | null>(null)
const status = ref<Record<string, unknown> | null>(null)

const verifyLoading = ref(false)
const repairLoading = ref<null | 'debt_symmetry' | 'trust_limits'>(null)

type IntegrityStatus = 'healthy' | 'warning' | 'critical'

function asIntegrityStatus(v: unknown): IntegrityStatus {
  if (v === 'healthy' || v === 'warning' || v === 'critical') return v
  return 'warning'
}

function tagTypeForIntegrityStatus(s: IntegrityStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'healthy') return 'success'
  if (s === 'warning') return 'warning'
  if (s === 'critical') return 'danger'
  return 'info'
}

const overallStatus = computed<IntegrityStatus>(() => asIntegrityStatus(status.value?.status))

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
}

const equivalents = computed<Record<string, unknown>>(() => {
  const s = asRecord(status.value)
  const v = s?.equivalents
  return asRecord(v) ?? {}
})

const alertsCount = computed(() => {
  const s = asRecord(status.value)
  const alerts = s?.alerts
  return Array.isArray(alerts) ? alerts.length : 0
})

type IssueKey = 'debt_symmetry' | 'trust_limits' | 'zero_sum'

const detectedIssues = computed<IssueKey[]>(() => {
  const found = new Set<IssueKey>()
  for (const [, eq] of Object.entries(equivalents.value)) {
    const inv = asRecord(asRecord(eq)?.invariants)
    if (!inv) continue

    const debt = asRecord(inv.debt_symmetry)
    const trust = asRecord(inv.trust_limits)
    const zero = asRecord(inv.zero_sum)

    if (debt?.passed === false) found.add('debt_symmetry')
    if (trust?.passed === false) found.add('trust_limits')
    if (zero?.passed === false) found.add('zero_sum')
  }

  // Show in a stable order.
  return ['zero_sum', 'trust_limits', 'debt_symmetry'].filter((k) => found.has(k as IssueKey)) as IssueKey[]
})

function issueLabel(k: IssueKey): string {
  if (k === 'zero_sum') return t('integrity.issue.zeroSum')
  if (k === 'trust_limits') return t('integrity.issue.trustLimits')
  return t('integrity.issue.debtSymmetry')
}

function tagTypeForPassed(passed: unknown): 'success' | 'danger' | 'info' {
  if (passed === true) return 'success'
  if (passed === false) return 'danger'
  return 'info'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    status.value = assertSuccess(await api.integrityStatus())
  } catch (e: unknown) {
    const f = formatApiError(e)
    error.value = f.hint ? `${f.title} — ${f.hint}` : f.title
  } finally {
    loading.value = false
  }
}

async function verify() {
  if (authStore.isReadOnly) {
    ElMessage.error(t('integrity.readOnlyVerificationDisabled'))
    return
  }
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
    assertSuccess(await api.integrityVerify())
    ElMessage.success(t('integrity.verify.finished'))
    await load()
  } catch (e: unknown) {
    const f = formatApiError(e)
    ElMessage.error(f.hint ? `${f.title} — ${f.hint}` : f.title)
  } finally {
    verifyLoading.value = false
  }
}

async function repairDebtSymmetry() {
  if (authStore.isReadOnly) {
    ElMessage.error(t('integrity.readOnlyRepairDisabled'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('integrity.repair.debtSymmetry.confirmText'),
      t('integrity.repair.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.repair'),
        cancelButtonText: t('common.cancel'),
      },
    )
  } catch {
    return
  }

  repairLoading.value = 'debt_symmetry'
  try {
    assertSuccess(await api.integrityRepairNetMutualDebts())
    ElMessage.success(t('integrity.repair.debtSymmetry.finished'))
    await load()
  } catch (e: unknown) {
    const f = formatApiError(e)
    ElMessage.error(f.hint ? `${f.title} — ${f.hint}` : f.title)
  } finally {
    repairLoading.value = null
  }
}

async function repairTrustLimits() {
  if (authStore.isReadOnly) {
    ElMessage.error(t('integrity.readOnlyRepairDisabled'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('integrity.repair.trustLimits.confirmText'),
      t('integrity.repair.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.repair'),
        cancelButtonText: t('common.cancel'),
      },
    )
  } catch {
    return
  }

  repairLoading.value = 'trust_limits'
  try {
    assertSuccess(await api.integrityRepairCapDebtsToTrustLimits())
    ElMessage.success(t('integrity.repair.trustLimits.finished'))
    await load()
  } catch (e: unknown) {
    const f = formatApiError(e)
    ElMessage.error(f.hint ? `${f.title} — ${f.hint}` : f.title)
  } finally {
    repairLoading.value = null
  }
}

onMounted(() => void load())
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
          :disabled="authStore.isReadOnly"
          type="primary"
          @click="verify"
        >
          {{ t('integrity.verify.action') }}
        </el-button>
      </div>
    </template>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <div v-else>
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
                {{ issueLabel(k) }}
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
              <li>{{ t('integrity.help.respond.stepRepair') }}</li>
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
              <li>{{ t('integrity.help.caseDebtSymmetry.step2') }}</li>
            </ul>

            <div class="actions">
              <el-button
                size="small"
                type="warning"
                :loading="repairLoading === 'debt_symmetry'"
                :disabled="authStore.isReadOnly"
                @click="repairDebtSymmetry"
              >
                {{ t('integrity.actions.repairNetMutualDebts') }}
              </el-button>
            </div>
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
            <ul class="helpList">
              <li>{{ t('integrity.help.caseTrustLimits.step1') }}</li>
              <li>{{ t('integrity.help.caseTrustLimits.step2') }}</li>
            </ul>

            <div class="actions">
              <el-button
                size="small"
                type="danger"
                :loading="repairLoading === 'trust_limits'"
                :disabled="authStore.isReadOnly"
                @click="repairTrustLimits"
              >
                {{ t('integrity.actions.repairCapDebts') }}
              </el-button>
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
        :column="2"
        border
      >
        <el-descriptions-item :label="t('common.status')">
          <el-tag :type="tagTypeForIntegrityStatus(overallStatus)">
            {{ t(`integrity.status.${overallStatus}`) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.lastCheck')">
          {{ status?.last_check }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.alerts')">
          {{ alertsCount }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('integrity.equivalents')">
          {{ Object.keys(equivalents).length }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider />

      <div class="sub">
        <TooltipLabel
          :label="t('integrity.section.equivalents')"
          :tooltip-text="t('integrity.help.equivalents')"
        />
      </div>
      <el-table
        :data="Object.entries(equivalents)"
        size="small"
        border
        table-layout="fixed"
        class="tbl geoTable"
      >
        <el-table-column
          :label="t('common.code')"
          width="110"
        >
          <template #default="scope">
            <span class="mono">{{ scope.row[0] }}</span>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('common.status')"
          width="120"
        >
          <template #default="scope">
            <el-tag :type="tagTypeForIntegrityStatus(asIntegrityStatus(scope.row[1]?.status))">
              {{ asIntegrityStatus(scope.row[1]?.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('integrity.columns.debtSymmetry')"
          min-width="260"
        >
          <template #default="scope">
            <div class="row">
              <el-tag
                :type="tagTypeForPassed(scope.row[1]?.invariants?.debt_symmetry?.passed)"
                effect="plain"
              >
                {{ t('common.passedPrefix') }} {{ scope.row[1]?.invariants?.debt_symmetry?.passed ?? t('common.na') }}
              </el-tag>
              <span class="muted">
                {{ t('common.violationsPrefix') }} {{ scope.row[1]?.invariants?.debt_symmetry?.violations ?? t('common.na') }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('integrity.columns.zeroSum')"
          width="140"
        >
          <template #default="scope">
            <el-tag
              :type="tagTypeForPassed(scope.row[1]?.invariants?.zero_sum?.passed)"
              effect="plain"
            >
              {{ scope.row[1]?.invariants?.zero_sum?.passed ? t('common.passed') : t('common.failed') }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('integrity.columns.trustLimits')"
          width="170"
        >
          <template #default="scope">
            <div class="row">
              <el-tag
                :type="tagTypeForPassed(scope.row[1]?.invariants?.trust_limits?.passed)"
                effect="plain"
              >
                {{ scope.row[1]?.invariants?.trust_limits?.passed ? t('common.passed') : t('common.failed') }}
              </el-tag>
              <span class="muted">
                {{ t('common.violationsPrefix') }} {{ scope.row[1]?.invariants?.trust_limits?.violations ?? t('common.na') }}
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-divider />

      <div class="sub">
        {{ t('integrity.section.rawPayload') }}
      </div>
      <pre class="json">{{ JSON.stringify(status, null, 2) }}</pre>
    </div>
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
  font-size: 12px;
  color: var(--el-text-color-secondary);
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
.actions {
  margin-top: 8px;
}
.helpHdr {
  font-size: 13px;
  font-weight: 600;
  margin-top: 6px;
  margin-bottom: 4px;
}
.helpText {
  font-size: 13px;
  color: var(--el-text-color-primary);
}
.helpList {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}
.helpList li {
  margin: 3px 0;
}
.helpNote {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
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
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
.json {
  margin: 0;
  font-size: 12px;
}
</style>
