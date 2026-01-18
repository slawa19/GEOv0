<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { t } from '../i18n'

type Equivalent = { code: string; precision: number; description: string; is_active: boolean }
type UsageCounts = { trustlines?: number; incidents?: number; debts?: number; integrity_checkpoints?: number }

const loading = ref(false)
const error = ref<string | null>(null)

const includeInactive = ref(false)
const items = ref<Equivalent[]>([])

const router = useRouter()
const authStore = useAuthStore()

const createOpen = ref(false)
const editOpen = ref(false)
const editing = ref<Equivalent | null>(null)

const createForm = reactive({ code: '', precision: 2, description: '', is_active: true })
const editForm = reactive({ precision: 2, description: '' })

const usageByCode = reactive<Record<string, UsageCounts | undefined>>({})
const usageLoadingByCode = reactive<Record<string, boolean | undefined>>({})

async function warmUsage(code: string) {
  const key = String(code || '').trim().toUpperCase()
  if (!key) return
  if (usageByCode[key]) return
  if (usageLoadingByCode[key]) return

  usageLoadingByCode[key] = true
  try {
    const usage = assertSuccess(await api.getEquivalentUsage(key))
    const u = usage as unknown as Record<string, unknown>
    usageByCode[key] = {
      trustlines: Number(u.trustlines ?? 0),
      incidents: typeof u.incidents === 'number' ? Number(u.incidents) : undefined,
      debts: typeof u.debts === 'number' ? Number(u.debts) : undefined,
      integrity_checkpoints:
        typeof u.integrity_checkpoints === 'number' ? Number(u.integrity_checkpoints) : undefined,
    }
  } catch {
    // best-effort only
  } finally {
    usageLoadingByCode[key] = false
  }
}

function onCellMouseEnter(row: Equivalent) {
  void warmUsage(row.code)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(await api.listEquivalents({ include_inactive: includeInactive.value }))
    items.value = data.items
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('equivalents.loadFailed')
  } finally {
    loading.value = false
  }
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
  try {
    const created = assertSuccess(
      await api.createEquivalent({
        code: createForm.code,
        precision: Number(createForm.precision),
        description: createForm.description,
        is_active: Boolean(createForm.is_active),
      }),
    ).created
    ElMessage.success(t('equivalents.created', { code: created.code }))
    createOpen.value = false
    includeInactive.value = true
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    ElMessage.error(msg || t('equivalents.createFailed'))
  }
}

async function saveEdit() {
  if (!editing.value) return
  try {
    const updated = assertSuccess(
      await api.updateEquivalent(editing.value.code, {
        precision: Number(editForm.precision),
        description: editForm.description,
      }),
    ).updated
    ElMessage.success(t('equivalents.updated', { code: updated.code }))
    editOpen.value = false
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    ElMessage.error(msg || t('equivalents.updateFailed'))
  }
}

async function setActive(row: Equivalent, next: boolean) {
  let reason: string
  try {
    reason = await ElMessageBox.prompt(
      t('common.reasonRequired'),
      next ? `${t('common.activate')} ${row.code}` : `${t('common.deactivate')} ${row.code}`,
      {
        confirmButtonText: next ? t('common.activate') : t('common.deactivate'),
        cancelButtonText: t('common.cancel'),
        inputPlaceholder: t('equivalents.reasonPlaceholder.activate'),
        inputValidator: (v) => (String(v || '').trim().length > 0 ? true : t('common.reasonIsRequired')),
        type: 'warning',
      },
    ).then((r) => r.value)
  } catch {
    return
  }

  try {
    assertSuccess(await api.setEquivalentActive(row.code, next, reason))
    ElMessage.success(next ? t('equivalents.activated', { code: row.code }) : t('equivalents.deactivated', { code: row.code }))
    includeInactive.value = true
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    ElMessage.error(msg || t('equivalents.updateFailed'))
  }
}

async function deleteEq(row: Equivalent) {
  let usageLine = ''
  try {
    const usage = assertSuccess(await api.getEquivalentUsage(row.code))
    const u = usage as unknown as Record<string, unknown>
    const tl = Number(u.trustlines ?? 0)
    const inc = u.incidents
    const debts = u.debts
    const ic = u.integrity_checkpoints
    const parts: string[] = []
    parts.push(t('equivalents.delete.usage.trustlines', { n: tl }))
    if (typeof inc === 'number') parts.push(t('equivalents.delete.usage.incidents', { n: inc }))
    if (typeof debts === 'number') parts.push(t('equivalents.delete.usage.debts', { n: debts }))
    if (typeof ic === 'number') parts.push(t('equivalents.delete.usage.integrityCheckpoints', { n: ic }))
    usageLine = parts.length ? t('equivalents.delete.usage.usedBy', { parts: parts.join(', ') }) : ''
  } catch {
    usageLine = ''
  }

  let reason: string
  try {
    reason = await ElMessageBox.prompt(
      [usageLine, t('equivalents.warning.deletePermanent'), '', t('common.reasonRequired')]
        .filter(Boolean)
        .join('\n'),
      t('equivalents.delete.title', { code: row.code }),
      {
        confirmButtonText: t('common.delete'),
        cancelButtonText: t('common.cancel'),
        inputPlaceholder: t('equivalents.reasonPlaceholder.delete'),
        inputValidator: (v) => (String(v || '').trim().length > 0 ? true : t('common.reasonIsRequired')),
        type: 'warning',
      },
    ).then((r) => r.value)
  } catch {
    return
  }

  try {
    assertSuccess(await api.deleteEquivalent(row.code, reason))
    ElMessage.success(t('equivalents.deleted', { code: row.code }))
    includeInactive.value = true
    await load()
  } catch (e: unknown) {
    const err = e as { message?: unknown; details?: Record<string, unknown> }
    const details = err?.details
    const tl = details?.trustlines
    const inc = details?.incidents
    const debts = details?.debts
    const ic = details?.integrity_checkpoints
    const msg = e instanceof Error ? e.message : String(err?.message ?? e)

    if ([tl, inc, debts, ic].some((v) => typeof v === 'number')) {
      ElMessage.error(
        t('equivalents.deleteFailedWithDetails', {
          msg: msg || t('equivalents.deleteFailed'),
          trustlines: Number(tl ?? 0),
          incidents: Number(inc ?? 0),
          debts: Number(debts ?? 0),
          ic: Number(ic ?? 0),
        }),
      )
    } else {
      ElMessage.error(msg || t('equivalents.deleteFailed'))
    }
  }
}

function goAudit(row: Equivalent) {
  void router.push({ path: '/audit-log', query: { code: row.code, q: row.code } })
}

onMounted(() => void load())
watch(includeInactive, () => void load())

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
            :disabled="authStore.isReadOnly"
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

    <LoadErrorAlert
      v-if="error"
      :title="error"
      :busy="loading"
      @retry="load"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      :description="t('equivalents.none')"
    />

    <div v-else>
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
                <template v-if="typeof usageByCode[scope.row.code]!.debts === 'number' || typeof usageByCode[scope.row.code]!.integrity_checkpoints === 'number'">
                  {{
                    t('equivalents.usage.tlDebtsIc', {
                      trustlines: usageByCode[scope.row.code]!.trustlines ?? 0,
                      debts: usageByCode[scope.row.code]!.debts ?? 0,
                      ic: usageByCode[scope.row.code]!.integrity_checkpoints ?? 0,
                    })
                  }}
                </template>
                <template v-else>
                  {{
                    t('equivalents.usage.tlInc', {
                      trustlines: usageByCode[scope.row.code]!.trustlines ?? 0,
                      incidents: usageByCode[scope.row.code]!.incidents ?? 0,
                    })
                  }}
                </template>
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
                :disabled="authStore.isReadOnly"
                @click="openEdit(scope.row)"
              >
                {{ t('common.edit') }}
              </el-button>
              <el-button
                v-if="scope.row.is_active"
                size="small"
                type="warning"
                :disabled="authStore.isReadOnly"
                @click="setActive(scope.row, false)"
              >
                {{ t('common.deactivate') }}
              </el-button>
              <el-button
                v-else
                size="small"
                type="success"
                :disabled="authStore.isReadOnly"
                @click="setActive(scope.row, true)"
              >
                {{ t('common.activate') }}
              </el-button>
              <el-button
                v-if="!scope.row.is_active"
                size="small"
                type="danger"
                :disabled="authStore.isReadOnly"
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
    </div>
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
        <el-input-number
          v-model="createForm.precision"
          :min="0"
          :max="18"
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
        :disabled="authStore.isReadOnly"
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
        <el-input-number
          v-model="editForm.precision"
          :min="0"
          :max="18"
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
        :disabled="authStore.isReadOnly"
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
