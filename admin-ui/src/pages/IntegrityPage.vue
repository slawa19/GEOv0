<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { formatApiError } from '../api/errorFormat'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'

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

const equivalents = computed<Record<string, any>>(() => {
  const v = (status.value as any)?.equivalents
  return (v && typeof v === 'object') ? v : {}
})

type IssueKey = 'debt_symmetry' | 'trust_limits' | 'zero_sum'

const detectedIssues = computed<IssueKey[]>(() => {
  const found = new Set<IssueKey>()
  for (const [, eq] of Object.entries(equivalents.value)) {
    const inv = (eq as any)?.invariants
    if (!inv || typeof inv !== 'object') continue
    if (inv.debt_symmetry?.passed === false) found.add('debt_symmetry')
    if (inv.trust_limits?.passed === false) found.add('trust_limits')
    if (inv.zero_sum?.passed === false) found.add('zero_sum')
  }

  // Show in a stable order.
  return ['zero_sum', 'trust_limits', 'debt_symmetry'].filter((k) => found.has(k as IssueKey)) as IssueKey[]
})

function issueLabel(k: IssueKey): string {
  if (k === 'zero_sum') return 'Zero-sum'
  if (k === 'trust_limits') return 'Trust limits'
  return 'Debt symmetry'
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
  } catch (e: any) {
    const f = formatApiError(e)
    error.value = f.hint ? `${f.title} — ${f.hint}` : f.title
  } finally {
    loading.value = false
  }
}

async function verify() {
  if (authStore.isReadOnly) {
    ElMessage.error('Read-only role: verification is disabled')
    return
  }
  try {
    await ElMessageBox.confirm(
      'Run integrity verification now? This may take a few seconds.',
      'Confirm verification',
      {
        type: 'warning',
        confirmButtonText: 'Run',
        cancelButtonText: 'Cancel',
      },
    )
  } catch {
    return
  }

  verifyLoading.value = true
  try {
    assertSuccess(await api.integrityVerify())
    ElMessage.success('Integrity verification finished')
    await load()
  } catch (e: any) {
    const f = formatApiError(e)
    ElMessage.error(f.hint ? `${f.title} — ${f.hint}` : f.title)
  } finally {
    verifyLoading.value = false
  }
}

async function repairDebtSymmetry() {
  if (authStore.isReadOnly) {
    ElMessage.error('Read-only role: repair actions are disabled')
    return
  }
  try {
    await ElMessageBox.confirm(
      'Repair will modify debt records in the database by netting mutual debts (A→B and B→A) into a single directed debt. Continue?',
      'Confirm repair',
      {
        type: 'warning',
        confirmButtonText: 'Repair',
        cancelButtonText: 'Cancel',
      },
    )
  } catch {
    return
  }

  repairLoading.value = 'debt_symmetry'
  try {
    assertSuccess(await api.integrityRepairNetMutualDebts())
    ElMessage.success('Repair finished: mutual debts were netted')
    await load()
  } catch (e: any) {
    const f = formatApiError(e)
    ElMessage.error(f.hint ? `${f.title} — ${f.hint}` : f.title)
  } finally {
    repairLoading.value = null
  }
}

async function repairTrustLimits() {
  if (authStore.isReadOnly) {
    ElMessage.error('Read-only role: repair actions are disabled')
    return
  }
  try {
    await ElMessageBox.confirm(
      'Repair will modify debt records in the database by capping or removing debts that exceed trust limits. Continue?',
      'Confirm repair',
      {
        type: 'warning',
        confirmButtonText: 'Repair',
        cancelButtonText: 'Cancel',
      },
    )
  } catch {
    return
  }

  repairLoading.value = 'trust_limits'
  try {
    assertSuccess(await api.integrityRepairCapDebtsToTrustLimits())
    ElMessage.success('Repair finished: debts were adjusted to trust limits')
    await load()
  } catch (e: any) {
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
          label="Integrity"
          tooltip-key="nav.integrity"
        />
        <el-button
          :loading="verifyLoading"
          :disabled="authStore.isReadOnly"
          type="primary"
          @click="verify"
        >
          Verify
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
          <span class="helpTitle">Integrity status: what it means and what to do</span>
        </template>

        <div class="help">
          <div
            v-if="overallStatus === 'healthy'"
            class="helpText"
          >
            All checked invariants pass: no exceeded limits, balanced totals, and no mutual debts.
          </div>

          <div
            v-else
            class="helpText"
          >
            Integrity checks found problems that should be investigated.
          </div>

          <div
            v-if="detectedIssues.length"
            class="helpDetected"
          >
            <div class="helpHdr">
              Detected issues
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
              How to respond (non-technical)
            </div>
            <ul class="helpList">
              <li>Click <span class="mono">Verify</span> to re-check the database right now and refresh the results.</li>
              <li>Review <span class="mono">Alerts</span> and the table below to see which equivalent is affected.</li>
              <li>Try the available <span class="mono">Repair</span> action (only for detected issue types) and re-run <span class="mono">Verify</span>.</li>
            </ul>
          </div>

          <el-divider class="helpDivider" />

          <div
            v-if="overallStatus !== 'healthy' && detectedIssues.length"
            class="helpHdr"
          >
            Interpretation & fixes (only for detected issues)
          </div>

          <div
            v-if="detectedIssues.includes('debt_symmetry')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="warning">
                Warning
              </el-tag>
              <span class="helpCaseName">Debt symmetry (mutual debts)</span>
            </div>
            <div class="helpText">
              This means for some participant pairs both debts exist at the same time: <span class="mono">A → B</span> and <span class="mono">B → A</span>.
              In a consistent ledger, these should be netted so that only one directed debt remains.
            </div>
            <ul class="helpList">
              <li>Identify affected pairs in the <span class="mono">Debt symmetry</span> column (<span class="mono">violations</span>).</li>
              <li>Use <span class="mono">Repair: net mutual debts</span> to automatically net the pairs into a single directed debt.</li>
            </ul>

            <div class="actions">
              <el-button
                size="small"
                type="warning"
                :loading="repairLoading === 'debt_symmetry'"
                :disabled="authStore.isReadOnly"
                @click="repairDebtSymmetry"
              >
                Repair: net mutual debts
              </el-button>
            </div>
          </div>

          <div
            v-if="detectedIssues.includes('trust_limits')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="danger">
                Critical
              </el-tag>
              <span class="helpCaseName">Trust limits exceeded</span>
            </div>
            <div class="helpText">
              Some debts are larger than the allowed trust limit for the same edge. This can break routing and risk controls.
            </div>
            <ul class="helpList">
              <li>Use <span class="mono">Repair: cap debts to trust limits</span> to automatically adjust (or remove) violating debts.</li>
              <li>Re-run <span class="mono">Verify</span> after repair to confirm the invariant passes.</li>
            </ul>

            <div class="actions">
              <el-button
                size="small"
                type="danger"
                :loading="repairLoading === 'trust_limits'"
                :disabled="authStore.isReadOnly"
                @click="repairTrustLimits"
              >
                Repair: cap debts to trust limits
              </el-button>
            </div>
          </div>

          <div
            v-if="detectedIssues.includes('zero_sum')"
            class="helpCase"
          >
            <div class="helpCaseTitle">
              <el-tag type="danger">
                Critical
              </el-tag>
              <span class="helpCaseName">Zero-sum violated</span>
            </div>
            <div class="helpText">
              Total balances are not self-consistent (the overall sum is not zero). This strongly indicates data corruption or missing/duplicated edges.
            </div>
            <ul class="helpList">
              <li>Stop balance-impacting operations and take a database backup/snapshot.</li>
              <li>Run <span class="mono">Verify</span> again to confirm it is reproducible (not a transient write state).</li>
              <li>Then proceed with controlled reconciliation (replay/rebuild) according to your operational playbook.</li>
            </ul>
          </div>
        </div>
      </el-alert>

      <el-descriptions
        :column="2"
        border
      >
        <el-descriptions-item label="Status">
          <el-tag :type="tagTypeForIntegrityStatus(overallStatus)">
            {{ overallStatus }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Last Check">
          {{ status?.last_check }}
        </el-descriptions-item>
        <el-descriptions-item label="Alerts">
          {{ (status as any)?.alerts?.length ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="Equivalents">
          {{ Object.keys(equivalents).length }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider />

      <div class="sub">
        Equivalents
      </div>
      <el-table
        :data="Object.entries(equivalents)"
        size="small"
        border
        table-layout="fixed"
        class="tbl"
      >
        <el-table-column
          label="Code"
          width="110"
        >
          <template #default="scope">
            <span class="mono">{{ scope.row[0] }}</span>
          </template>
        </el-table-column>

        <el-table-column
          label="Status"
          width="120"
        >
          <template #default="scope">
            <el-tag :type="tagTypeForIntegrityStatus(asIntegrityStatus(scope.row[1]?.status))">
              {{ asIntegrityStatus(scope.row[1]?.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          label="Debt symmetry"
          min-width="260"
        >
          <template #default="scope">
            <div class="row">
              <el-tag
                :type="tagTypeForPassed(scope.row[1]?.invariants?.debt_symmetry?.passed)"
                effect="plain"
              >
                passed: {{ scope.row[1]?.invariants?.debt_symmetry?.passed ?? '—' }}
              </el-tag>
              <span class="muted">
                violations: {{ scope.row[1]?.invariants?.debt_symmetry?.violations ?? '—' }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          label="Zero-sum"
          width="140"
        >
          <template #default="scope">
            <el-tag
              :type="tagTypeForPassed(scope.row[1]?.invariants?.zero_sum?.passed)"
              effect="plain"
            >
              {{ scope.row[1]?.invariants?.zero_sum?.passed ? 'passed' : 'failed' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          label="Trust limits"
          width="170"
        >
          <template #default="scope">
            <div class="row">
              <el-tag
                :type="tagTypeForPassed(scope.row[1]?.invariants?.trust_limits?.passed)"
                effect="plain"
              >
                {{ scope.row[1]?.invariants?.trust_limits?.passed ? 'passed' : 'failed' }}
              </el-tag>
              <span class="muted">
                violations: {{ scope.row[1]?.invariants?.trust_limits?.violations ?? '—' }}
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-divider />

      <div class="sub">
        Raw Payload
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
