<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { ApiException } from '../api/apiException'
import type { AdminEquivalentUsageResponse } from '../api/adminContracts'
import { describeError } from '../api/describeError'
import TooltipLabel from '../ui/TooltipLabel.vue'
import ListState from '../ui/ListState.vue'
import { promptReason } from '../ui/promptReason'
import { t } from '../i18n'
import { useLatestRequest } from '../composables/useLatestRequest'
import { useBusyKeys } from '../composables/useBusyKeys'
import { toLocationQueryRaw } from '../router/query'
import type { Equivalent } from '../types/domain'
import { normalizeEquivalentCode } from '../utils/equivalent'

// 032 S5 (A-4): the server never sent `incidents` here (`AdminEquivalentUsageResponse` is strict); the dead key is gone.
type UsageCounts = Pick<AdminEquivalentUsageResponse, 'trustlines' | 'debts' | 'integrity_checkpoints'>

const loading = ref(false)
const error = ref<string | null>(null)

const includeInactive = ref(false)
const items = ref<Equivalent[]>([])
const loadRequests = useLatestRequest()
let pageActive = true

const router = useRouter()

const createOpen = ref(false)
const editOpen = ref(false)
const editing = ref<Equivalent | null>(null)
// A request in flight is not sent again: the dialog's button is busy until the answer (or the refusal) is in.
const creating = ref(false)
const saving = ref(false)

const createForm = reactive({ code: '', precision: 2, description: '', is_active: true })
const editForm = reactive({ precision: 2, description: '' })

const usageByCode = reactive<Record<string, UsageCounts | undefined>>({})
const usageLoadingByCode = reactive<Record<string, boolean | undefined>>({})
// What the page knows about a code's usage is dropped when the code is changed; an answer that was already on its
// way when that happened describes the old state and is not kept.
const usageGeneration = new Map<string, number>()

function forgetUsage(code: string) {
  const key = normalizeEquivalentCode(code)
  usageGeneration.set(key, (usageGeneration.get(key) ?? 0) + 1)
  delete usageByCode[key]
  usageLoadingByCode[key] = false
}

async function warmUsage(code: string) {
  const key = normalizeEquivalentCode(code)
  if (!key) return
  if (usageByCode[key]) return
  if (usageLoadingByCode[key]) return

  const generation = usageGeneration.get(key) ?? 0
  usageLoadingByCode[key] = true
  try {
    const usage = await api.getEquivalentUsage(key)
    if (!pageActive || (usageGeneration.get(key) ?? 0) !== generation) return
    usageByCode[key] = {
      trustlines: usage.trustlines,
      debts: usage.debts,
      integrity_checkpoints: usage.integrity_checkpoints,
    }
  } catch {
    // best-effort only
  } finally {
    if (pageActive && (usageGeneration.get(key) ?? 0) === generation) usageLoadingByCode[key] = false
  }
}

function onCellMouseEnter(row: Equivalent) {
  void warmUsage(row.code)
}

async function load() {
  const request = loadRequests.begin()
  const requestIncludeInactive = includeInactive.value
  loading.value = true
  error.value = null
  try {
    const data = await api.listEquivalents({ include_inactive: requestIncludeInactive })
    if (!request.isCurrent()) return
    items.value = data.items
  } catch (e: unknown) {
    if (!request.isCurrent()) return
    error.value = describeError(e, 'equivalents.loadFailed').text
  } finally {
    if (request.isCurrent()) loading.value = false
  }
}

// After a change the list shows every equivalent, so the one just stopped does not vanish from under the operator.
// Switching the filter on loads by itself (its watcher); when it is already on, load here - never both.
async function reloadShowingAll() {
  if (includeInactive.value) await load()
  else includeInactive.value = true
}

function openCreate() {
  createForm.code = ''
  createForm.precision = 2
  createForm.description = ''
  createForm.is_active = true
  createOpen.value = true
}

function openEdit(row: Equivalent) {
  editing.value = row
  editForm.precision = row.precision
  editForm.description = row.description
  editOpen.value = true
}

async function createEq() {
  if (creating.value) return
  creating.value = true
  try {
    const created = (
      await api.createEquivalent({
        code: createForm.code,
        precision: Number(createForm.precision),
        description: createForm.description,
        is_active: Boolean(createForm.is_active),
      })
    ).created
    if (!pageActive) return
    forgetUsage(created.code)
    ElMessage.success(t('equivalents.created', { code: created.code }))
    createOpen.value = false
    await reloadShowingAll()
  } catch (e: unknown) {
    if (!pageActive) return
    ElMessage.error(describeError(e, 'equivalents.createFailed').text)
  } finally {
    creating.value = false
  }
}

async function saveEdit() {
  if (!editing.value || saving.value) return
  saving.value = true
  try {
    const updated = (
      await api.updateEquivalent(editing.value.code, {
        precision: Number(editForm.precision),
        description: editForm.description,
      })
    ).updated
    if (!pageActive) return
    forgetUsage(updated.code)
    ElMessage.success(t('equivalents.updated', { code: updated.code }))
    editOpen.value = false
    await load()
  } catch (e: unknown) {
    if (!pageActive) return
    ElMessage.error(describeError(e, 'equivalents.updateFailed').text)
  } finally {
    saving.value = false
  }
}

// Stopping, starting and deleting an equivalent run once at a time per code, prompt included: the row's buttons stay
// busy until the answer is in, so a second press cannot send the same change again.
const busy = useBusyKeys()

function setActive(row: Equivalent, next: boolean) {
  return busy.run(row.code, () => runSetActive(row, next))
}

function deleteEq(row: Equivalent) {
  return busy.run(row.code, () => runDelete(row))
}

async function runSetActive(row: Equivalent, next: boolean) {
  const reason = await promptReason(
    `${next ? t('common.activate') : t('common.deactivate')} ${row.code}`,
    '',
    next ? 'common.activate' : 'common.deactivate',
    'equivalents.reasonPlaceholder.activate',
  )
  if (!reason || !pageActive) return
  try {
    const updated = (await api.setEquivalentActive(row.code, next, reason)).updated
    if (!pageActive) return
    forgetUsage(row.code)
    const index = items.value.findIndex((item) => item.code === row.code)
    if (index >= 0) items.value[index] = updated
    ElMessage.success(next ? t('equivalents.activated', { code: row.code }) : t('equivalents.deactivated', { code: row.code }))
    await reloadShowingAll()
  } catch (e: unknown) {
    if (!pageActive) return
    ElMessage.error(describeError(e, 'equivalents.updateFailed').text)
  }
}

// What the server says uses an equivalent it refused to delete. `requestJson` keeps the body's `details` one level
// down (`ApiException.details.details`, next to the url and status it adds); `null` when the refusal names no counters
// (a row that outlived the count, `referenced_by_existing_rows`).
function refusalCounts(e: unknown): UsageCounts | null {
  if (!(e instanceof ApiException)) return null
  const outer = e.details && typeof e.details === 'object' ? (e.details as Record<string, unknown>) : null
  const inner = outer?.details && typeof outer.details === 'object' ? (outer.details as Record<string, unknown>) : null
  if (!inner) return null
  const { trustlines, debts, integrity_checkpoints: checkpoints } = inner
  if (typeof trustlines !== 'number' || typeof debts !== 'number' || typeof checkpoints !== 'number') return null
  return { trustlines, debts, integrity_checkpoints: checkpoints }
}

async function runDelete(row: Equivalent) {
  let usageLine = ''
  try {
    const usage = await api.getEquivalentUsage(row.code)
    usageLine = t('equivalents.delete.usage.usedBy', {
      parts: [
        t('equivalents.delete.usage.trustlines', { n: usage.trustlines }),
        t('equivalents.delete.usage.debts', { n: usage.debts }),
        t('equivalents.delete.usage.integrityCheckpoints', { n: usage.integrity_checkpoints }),
      ].join(', '),
    })
  } catch {
    usageLine = ''
  }

  if (!pageActive) return

  const reason = await promptReason(
    t('equivalents.delete.title', { code: row.code }),
    [usageLine, t('equivalents.warning.deletePermanent')].filter(Boolean).join('\n'),
    'common.delete',
    'equivalents.reasonPlaceholder.delete',
  )
  if (!reason || !pageActive) return
  try {
    await api.deleteEquivalent(row.code, reason)
    if (!pageActive) return
    forgetUsage(row.code)
    ElMessage.success(t('equivalents.deleted', { code: row.code }))
    await reloadShowingAll()
  } catch (e: unknown) {
    if (!pageActive) return
    const msg = describeError(e, 'equivalents.deleteFailed').text
    const counts = refusalCounts(e)
    ElMessage.error(
      counts
        ? t('equivalents.deleteFailedWithDetails', {
            msg,
            trustlines: counts.trustlines,
            debts: counts.debts,
            ic: counts.integrity_checkpoints,
          })
        : msg,
    )
  }
}

function goAudit(row: Equivalent) {
  void router.push({
    path: '/audit-log',
    query: toLocationQueryRaw({ code: row.code, q: row.code }),
  })
}

onMounted(() => void load())
watch(includeInactive, () => void load())
onBeforeUnmount(() => {
  pageActive = false
})

const activeCount = computed(() => items.value.filter((e) => e.is_active).length)
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('equivalents.title')"
          tooltip-key="nav.equivalents"
        />
        <div class="hdr__actions">
          <el-button
            type="primary"
            @click="openCreate"
          >
            {{ t('common.create') }}
          </el-button>
          <el-switch
            v-model="includeInactive"
            :active-text="t('equivalents.includeInactive')"
          />
          <el-tag type="info">
            {{ t('equivalents.activeCount', { n: activeCount }) }}
          </el-tag>
        </div>
      </div>
    </template>

    <ListState
      :error="error"
      :loading="loading"
      :empty="items.length === 0"
      :empty-text="t('equivalents.none')"
      @retry="load"
    >
      <el-table
        :data="items"
        size="small"
        table-layout="fixed"
        class="geoTable"
        @cell-mouse-enter="onCellMouseEnter"
      >
        <el-table-column
          prop="code"
          :label="t('common.code')"
          width="240"
        >
          <template #default="scope">
            <div class="code">
              <div class="code__main">
                {{ scope.row.code }}
              </div>
              <div
                v-if="usageByCode[scope.row.code]"
                class="code__sub"
              >
                {{
                  t('equivalents.usage.tlDebtsIc', {
                    trustlines: usageByCode[scope.row.code]!.trustlines,
                    debts: usageByCode[scope.row.code]!.debts,
                    ic: usageByCode[scope.row.code]!.integrity_checkpoints,
                  })
                }}
              </div>
              <div
                v-else-if="usageLoadingByCode[scope.row.code]"
                class="code__sub"
              >
                {{ t('equivalents.usage.loading') }}
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="precision"
          :label="t('common.precision')"
          width="80"
          align="center"
          header-align="center"
        />
        <el-table-column
          prop="description"
          :label="t('common.description')"
          min-width="300"
          show-overflow-tooltip
        />
        <el-table-column
          prop="is_active"
          :label="t('common.active')"
          width="90"
          align="center"
          header-align="center"
        >
          <template #default="scope">
            <el-tag
              v-if="scope.row.is_active"
              type="success"
            >
              {{ t('common.yes') }}
            </el-tag>
            <el-tag
              v-else
              type="info"
            >
              {{ t('common.no') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('equivalents.columns.actions')"
          width="340"
        >
          <template #default="scope">
            <div class="eqActions">
              <el-button
                size="small"
                @click="openEdit(scope.row)"
              >
                {{ t('common.edit') }}
              </el-button>
              <el-button
                v-if="scope.row.is_active"
                size="small"
                type="warning"
                :loading="busy.has(scope.row.code)"
                @click="setActive(scope.row, false)"
              >
                {{ t('common.deactivate') }}
              </el-button>
              <el-button
                v-else
                size="small"
                type="success"
                :loading="busy.has(scope.row.code)"
                @click="setActive(scope.row, true)"
              >
                {{ t('common.activate') }}
              </el-button>
              <el-button
                v-if="!scope.row.is_active"
                size="small"
                type="danger"
                :loading="busy.has(scope.row.code)"
                @click="deleteEq(scope.row)"
              >
                {{ t('common.delete') }}
              </el-button>
              <el-button
                size="small"
                @click="goAudit(scope.row)"
              >
                {{ t('common.audit') }}
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </ListState>
  </el-card>

  <el-dialog
    v-model="createOpen"
    :title="t('equivalents.dialog.createTitle')"
    width="520"
  >
    <el-form label-width="120">
      <el-form-item :label="t('common.code')">
        <el-input
          v-model="createForm.code"
          :placeholder="t('equivalents.form.codePlaceholder')"
          style="width: 200px"
        />
      </el-form-item>
      <el-form-item :label="t('common.precision')">
        <!-- 0..8: the API and the canon narrowed the domain (012 / S1, 2026-08-25) to the
             storage scale of Numeric(20, 8) and the protocol's own 0-8. Offering 18 here
             would let an operator submit a value the server now answers 422 to. -->
        <el-input-number
          v-model="createForm.precision"
          :min="0"
          :max="8"
        />
      </el-form-item>
      <el-form-item :label="t('common.description')">
        <el-input
          v-model="createForm.description"
          :placeholder="t('equivalents.form.descriptionPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('common.active')">
        <el-switch v-model="createForm.is_active" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createOpen = false">
        {{ t('common.cancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="creating"
        @click="createEq"
      >
        {{ t('common.create') }}
      </el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="editOpen"
    :title="t('equivalents.dialog.editTitle')"
    width="520"
  >
    <div
      v-if="editing"
      class="muted"
    >
      {{ t('equivalents.dialog.editing', { code: editing.code }) }}
    </div>
    <el-form label-width="120">
      <el-form-item :label="t('common.precision')">
        <!-- 0..8, same reason as the create dialog above. -->
        <el-input-number
          v-model="editForm.precision"
          :min="0"
          :max="8"
        />
      </el-form-item>
      <el-form-item :label="t('common.description')">
        <el-input v-model="editForm.description" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editOpen = false">
        {{ t('common.cancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        @click="saveEdit"
      >
        {{ t('common.save') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hdr__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.mb {
  margin-bottom: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-sub);
  margin-bottom: 10px;
}

.code {
  line-height: 1.15;
}

.code__main {
  font-weight: 600;
}

.code__sub {
  margin-top: 2px;
  font-size: var(--geo-font-size-sub);
  color: var(--el-text-color-secondary);
}

.eqActions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
