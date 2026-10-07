<script setup lang="ts">
import { computed } from 'vue'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import GraphSearchBar from './GraphSearchBar.vue'
import { t } from '../../i18n'
import { isUnitIntervalDecimalString } from '../../utils/decimal'

type ToolbarTab = 'filters' | 'display'
type LayoutName = 'fcose' | 'grid' | 'circle'

type Option = {
  label: string
  value: string
}

type ParticipantSuggestion = {
  value: string
  pid: string
}

type LabelPart = 'name' | 'pid'

type FetchSuggestionsFn = (query: string, cb: (results: ParticipantSuggestion[]) => void) => void

// What the operator edits here is the page state, passed as `v-model`s (032 S7, D-15: these were eighteen
// hand-written computed get/set pairs over a prop and an `update:` emit each).
const toolbarTab = defineModel<ToolbarTab>('toolbarTab', { required: true })

// Filters
const eq = defineModel<string>('eq', { required: true })
const statusFilter = defineModel<string[]>('statusFilter', { required: true })
const threshold = defineModel<string>('threshold', { required: true })
const typeFilter = defineModel<string[]>('typeFilter', { required: true })
const minDegree = defineModel<number>('minDegree', { required: true })

// Display
const layoutName = defineModel<LayoutName>('layoutName', { required: true })
const layoutSpacing = defineModel<number>('layoutSpacing', { required: true })
const businessLabelParts = defineModel<LabelPart[]>('businessLabelParts', { required: true })
const personLabelParts = defineModel<LabelPart[]>('personLabelParts', { required: true })
const showLabels = defineModel<boolean>('showLabels', { required: true })
const autoLabelsByZoom = defineModel<boolean>('autoLabelsByZoom', { required: true })
const hideIsolates = defineModel<boolean>('hideIsolates', { required: true })
const showLegend = defineModel<boolean>('showLegend', { required: true })

// Search / focus
const searchQuery = defineModel<string>('searchQuery', { required: true })
const focusPid = defineModel<string>('focusPid', { required: true })
const focusMode = defineModel<boolean>('focusMode', { required: true })
const focusDepth = defineModel<1 | 2>('focusDepth', { required: true })

const props = defineProps<{
  availableEquivalents: string[]
  /** 028 F-028-49 (C2): the equivalent was chosen by the page (most active trust lines), not by the operator. */
  eqAutoSelected?: boolean
  statuses: Option[]
  layoutOptions: Option[]
  /** The autocomplete of the search box calls this with a callback; it is a function by the contract of that component. */
  fetchSuggestions: FetchSuggestionsFn
  canFind: boolean
  focusRootPid: string
  canUseSelectedForFocus: boolean
}>()

const emit = defineEmits<{
  focusSearch: []
  useSelectedForFocus: []
  clearFocus: []
}>()

const thresholdValid = computed(() => isUnitIntervalDecimalString(threshold.value))

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n))
}

function widthChFromLabels(labels: string[], minCh: number, maxCh: number, extraCh = 4): string {
  const longest = Math.max(0, ...labels.map((s) => (s ?? '').length))
  return `${clamp(longest + extraCh, minCh, maxCh)}ch`
}

const eqFieldWidth = computed(() => widthChFromLabels(props.availableEquivalents, 10, 18, 2))
const statusFieldWidth = computed(() => widthChFromLabels(props.statuses.map((s) => s.label), 14, 26, 6))
const thresholdFieldWidth = computed(() => '18ch')
const minDegreeFieldWidth = computed(() => '12ch')

const layoutFieldWidth = computed(() => widthChFromLabels(props.layoutOptions.map((o) => o.label), 12, 22, 6))
const focusDepthFieldWidth = computed(() =>
  widthChFromLabels([t('graph.navigate.depth1'), t('graph.navigate.depth2')], 10, 18, 4),
)
</script>

<template>
  <div class="toolbar">
    <el-tabs
      v-model="toolbarTab"
      type="card"
      class="toolbarTabs"
    >
      <el-tab-pane
        :label="t('graph.toolbar.filtersTab')"
        name="filters"
      >
        <div class="filtersLayout">
          <div class="filtersLayout__search">
            <GraphSearchBar
              v-model:search-query="searchQuery"
              v-model:focus-pid="focusPid"
              :can-find="canFind"
              :fetch-suggestions="fetchSuggestions"
              @focus-search="emit('focusSearch')"
            />
          </div>

          <div class="filtersLayout__focus">
            <div class="focusRow">
              <div class="focusRow__toggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.navigate.focus')"
                  :tooltip-text="t('graph.navigate.focusMode.tooltip')"
                />
                <el-switch
                  v-model="focusMode"
                  size="small"
                />
              </div>

              <div class="focusRow__controls">
                <el-select
                  v-model="focusDepth"
                  size="small"
                  class="ctl__field ctl__field--compact focus__depth"
                  :disabled="!focusMode"
                  :style="{ '--geo-ctl-width': focusDepthFieldWidth }"
                >
                  <el-option
                    :label="t('graph.navigate.depth1')"
                    :value="1"
                  />
                  <el-option
                    :label="t('graph.navigate.depth2')"
                    :value="2"
                  />
                </el-select>

                <el-tag
                  v-if="focusMode && focusRootPid"
                  type="info"
                  class="focus__tag"
                >
                  {{ focusRootPid }}
                </el-tag>

                <el-button
                  size="small"
                  :disabled="!canUseSelectedForFocus"
                  @click="emit('useSelectedForFocus')"
                >
                  {{ t('graph.navigate.useSelected') }}
                </el-button>
                <el-button
                  size="small"
                  :disabled="!focusMode"
                  @click="emit('clearFocus')"
                >
                  {{ t('graph.navigate.clear') }}
                </el-button>
              </div>
            </div>
          </div>

          <div class="filtersLayout__filters">
            <div class="filtersRow">
              <div class="ctl">
                <TooltipLabel
                  class="toolbarLabel ctl__label"
                  :label="t('graph.filters.equivalent')"
                  tooltip-key="graph.eq"
                />
                <el-select
                  v-model="eq"
                  size="small"
                  class="ctl__field ctl__field--compact"
                  :style="{ '--geo-ctl-width': eqFieldWidth }"
                  data-testid="graph-filter-eq"
                >
                  <el-option
                    v-for="c in availableEquivalents"
                    :key="c"
                    :label="c"
                    :value="c"
                  />
                </el-select>
                <el-tag
                  v-if="eqAutoSelected"
                  size="small"
                  type="info"
                  :title="t('graph.filters.equivalentAutoHint')"
                  data-testid="graph-eq-auto"
                >
                  {{ t('graph.filters.equivalentAuto') }}
                </el-tag>
              </div>

              <div class="ctl">
                <TooltipLabel
                  class="toolbarLabel ctl__label"
                  :label="t('graph.filters.status')"
                  tooltip-key="graph.status"
                />
                <el-select
                  v-model="statusFilter"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  size="small"
                  class="ctl__field ctl__field--compact"
                  :style="{ '--geo-ctl-width': statusFieldWidth }"
                >
                  <el-option
                    v-for="s in statuses"
                    :key="s.value"
                    :label="s.label"
                    :value="s.value"
                  />
                </el-select>
              </div>

              <div class="ctl">
                <TooltipLabel
                  class="toolbarLabel ctl__label"
                  :label="t('graph.filters.bottleneck')"
                  tooltip-key="graph.threshold"
                />
                <el-input
                  v-model="threshold"
                  size="small"
                  class="ctl__field ctl__field--compact"
                  :style="{ '--geo-ctl-width': thresholdFieldWidth }"
                  :aria-invalid="!thresholdValid"
                  :placeholder="t('graph.filters.bottleneckPlaceholder')"
                />
              </div>

              <div class="ctl">
                <TooltipLabel
                  class="toolbarLabel ctl__label"
                  :label="t('graph.filters.type')"
                  tooltip-key="graph.type"
                />
                <el-checkbox-group
                  v-model="typeFilter"
                  size="small"
                >
                  <el-checkbox-button value="person">
                    {{ t('participant.type.person') }}
                  </el-checkbox-button>
                  <el-checkbox-button value="business">
                    {{ t('participant.type.business') }}
                  </el-checkbox-button>
                </el-checkbox-group>
              </div>

              <div class="ctl">
                <TooltipLabel
                  class="toolbarLabel ctl__label"
                  :label="t('graph.filters.minDegree')"
                  tooltip-key="graph.minDegree"
                />
                <el-input-number
                  v-model="minDegree"
                  size="small"
                  :min="0"
                  :max="20"
                  controls-position="right"
                  class="ctl__field ctl__field--compact"
                  :style="{ '--geo-ctl-width': minDegreeFieldWidth }"
                />
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        :label="t('graph.toolbar.displayTab')"
        name="display"
      >
        <div class="displayGrid">
          <div class="ctl displayGrid__layout">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.display.layout')"
              tooltip-key="graph.layout"
            />
            <el-select
              v-model="layoutName"
              size="small"
              class="ctl__field ctl__field--compact"
              :style="{ '--geo-ctl-width': layoutFieldWidth }"
            >
              <el-option
                v-for="o in layoutOptions"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
          </div>

          <div class="ctl displayGrid__spacing">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.display.layoutSpacing')"
              tooltip-key="graph.spacing"
            />
            <el-slider
              v-model="layoutSpacing"
              :min="1"
              :max="3"
              :step="0.1"
              class="sliderField"
            />
          </div>

          <div class="displayGrid__labels ctlGroup ctlGroup--labels">
            <div class="ctl">
              <TooltipLabel
                class="toolbarLabel ctl__label"
                :label="t('graph.display.businessLabels')"
                tooltip-key="graph.labels"
              />
              <el-checkbox-group
                v-model="businessLabelParts"
                size="small"
              >
                <el-checkbox-button value="name">
                  {{ t('graph.display.labelPart.name') }}
                </el-checkbox-button>
                <el-checkbox-button value="pid">
                  {{ t('graph.display.labelPart.pid') }}
                </el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="ctl">
              <TooltipLabel
                class="toolbarLabel ctl__label"
                :label="t('graph.display.personLabels')"
                tooltip-key="graph.labels"
              />
              <el-checkbox-group
                v-model="personLabelParts"
                size="small"
              >
                <el-checkbox-button value="name">
                  {{ t('graph.display.labelPart.name') }}
                </el-checkbox-button>
                <el-checkbox-button value="pid">
                  {{ t('graph.display.labelPart.pid') }}
                </el-checkbox-button>
              </el-checkbox-group>
            </div>
          </div>

          <div class="displayGrid__toggles displayToggleRow">
            <div class="displayToggleGroup">
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.labels')"
                  tooltip-key="graph.labels"
                />
                <el-switch
                  v-model="showLabels"
                  size="small"
                />
              </div>
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.autoLabels')"
                  tooltip-key="graph.labels"
                />
                <el-switch
                  v-model="autoLabelsByZoom"
                  size="small"
                />
              </div>
            </div>

            <div class="displayToggleGroup">
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.hideIsolates')"
                  tooltip-key="graph.hideIsolates"
                />
                <el-switch
                  v-model="hideIsolates"
                  size="small"
                />
              </div>
            </div>

            <div class="displayToggleGroup">
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.legend')"
                  tooltip-key="graph.legend"
                />
                <el-switch
                  v-model="showLegend"
                  size="small"
                />
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Navigate tab removed: search moved into Filters, nav actions live in the page header. -->
    </el-tabs>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}

.toolbarTabs :deep(.el-tabs__header) {
  margin: 0 0 8px 0;
}

.toolbarTabs :deep(.el-tabs__content) {
  padding: 0;
}

.filtersLayout {
  display: grid;
  grid-template-columns: minmax(320px, 1fr) auto;
  grid-template-areas:
    'search filters'
    'focus  filters';
  gap: 10px 18px;
  align-items: start;
}

.filtersLayout__search {
  grid-area: search;
  min-width: 0;
}

.filtersLayout__focus {
  grid-area: focus;
  min-width: 0;
}

.filtersLayout__filters {
  grid-area: filters;
  min-width: 0;
}

.filtersRow {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px 12px;
  align-items: start;
}

.ctl {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.toolbarLabel {
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
  color: var(--el-text-color-secondary);
}

.ctl__label {
  min-height: 18px;
}

.ctl__field {
  width: 100%;
  min-width: 0;
}

.ctl__field--compact {
  width: var(--geo-ctl-width, 180px);
}

.ctl__field--compact :deep(.el-autocomplete),
.ctl__field--compact :deep(.el-input),
.ctl__field--compact :deep(.el-input__wrapper),
.ctl__field--compact :deep(.el-select),
.ctl__field--compact :deep(.el-select__wrapper),
.ctl__field--compact :deep(.el-input-number) {
  width: 100%;
}

.ctl__field--compact :deep(.el-input-number .el-input),
.ctl__field--compact :deep(.el-input-number .el-input__wrapper) {
  width: 100%;
}

.ctl__hint {
  max-width: var(--geo-ctl-width, 180px);
  margin-top: 4px;
  font-size: var(--geo-font-size-sub);
  line-height: 1.2;
  color: var(--el-text-color-secondary);
}


.sliderField {
  width: clamp(220px, 22vw, 360px);
}

.displayGrid {
  display: grid;
  grid-template-areas:
    'layout spacing labels'
    'toggles toggles toggles';
  grid-template-columns: max-content minmax(240px, 1fr) max-content;
  gap: 10px 18px;
  align-items: start;
}

.displayGrid__layout {
  grid-area: layout;
  min-width: 0;
}

.displayGrid__spacing {
  grid-area: spacing;
  min-width: 0;
}

.displayGrid__labels {
  grid-area: labels;
}

.displayGrid__toggles {
  grid-area: toggles;
}

.ctlGroup--labels {
  display: grid;
  grid-template-columns: max-content max-content;
  gap: 10px 18px;
  align-items: start;
}

.displayToggleRow {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 10px 28px;
}

.displayToggleGroup {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
}

.displayToggle {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0;
}



.focusRow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
}

.focusRow__toggle {
  display: flex;
  align-items: center;
  gap: 10px;
}

.focusRow__controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.focus__depth {
  width: var(--geo-ctl-width, 120px);
}

.focus__tag {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 992px) {
  .filtersLayout {
    grid-template-columns: 1fr;
    grid-template-areas:
      'search'
      'focus'
      'filters';
  }

  .filtersRow {
    justify-content: flex-start;
  }
}

@media (max-width: 768px) {
  .filtersLayout {
    grid-template-columns: 1fr;
    grid-template-areas:
      'search'
      'focus'
      'filters';
  }

  .displayGrid {
    grid-template-areas:
      'layout'
      'spacing'
      'labels'
      'toggles';
    grid-template-columns: 1fr;
  }

  .ctlGroup--labels {
    grid-template-columns: 1fr;
  }

}
</style>
