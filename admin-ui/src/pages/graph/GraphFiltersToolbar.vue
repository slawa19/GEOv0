<script setup lang="ts">
import { computed } from 'vue'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import GraphSearchBar from './GraphSearchBar.vue'
import { t } from '../../i18n'

type ToolbarTab = 'filters' | 'display' | 'navigate'
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

type Props = {
  toolbarTab: ToolbarTab

  // Filters
  eq: string
  availableEquivalents: string[]
  statusFilter: string[]
  statuses: Option[]
  threshold: string
  typeFilter: string[]
  minDegree: number

  // Display
  layoutName: LayoutName
  layoutOptions: Option[]
  layoutSpacing: number
  businessLabelParts: LabelPart[]
  personLabelParts: LabelPart[]
  showLabels: boolean
  autoLabelsByZoom: boolean
  showIncidents: boolean
  hideIsolates: boolean
  showLegend: boolean

  // Navigate
  searchQuery: string
  focusPid: string
  fetchSuggestions: FetchSuggestionsFn
  canFind: boolean
  zoom: number

  focusMode: boolean
  focusDepth: 1 | 2
  focusRootPid: string
  canUseSelectedForFocus: boolean

  // Actions
  onFocusSearch: () => void
  onFit: () => void
  onRelayout: () => void
  onUseSelectedForFocus: () => void
  onClearFocusMode: () => void
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:toolbarTab', v: ToolbarTab): void
  (e: 'update:eq', v: string): void
  (e: 'update:statusFilter', v: string[]): void
  (e: 'update:threshold', v: string): void
  (e: 'update:typeFilter', v: string[]): void
  (e: 'update:minDegree', v: number): void

  (e: 'update:layoutName', v: LayoutName): void
  (e: 'update:layoutSpacing', v: number): void
  (e: 'update:businessLabelParts', v: LabelPart[]): void
  (e: 'update:personLabelParts', v: LabelPart[]): void
  (e: 'update:showLabels', v: boolean): void
  (e: 'update:autoLabelsByZoom', v: boolean): void
  (e: 'update:showIncidents', v: boolean): void
  (e: 'update:hideIsolates', v: boolean): void
  (e: 'update:showLegend', v: boolean): void

  (e: 'update:searchQuery', v: string): void
  (e: 'update:focusPid', v: string): void
  (e: 'update:zoom', v: number): void

  (e: 'update:focusMode', v: boolean): void
  (e: 'update:focusDepth', v: 1 | 2): void
}>()

const toolbarTabModel = computed({
  get: () => props.toolbarTab,
  set: (v) => emit('update:toolbarTab', v),
})

const eqModel = computed({
  get: () => props.eq,
  set: (v) => emit('update:eq', v),
})

const statusFilterModel = computed({
  get: () => props.statusFilter,
  set: (v) => emit('update:statusFilter', v),
})

const thresholdModel = computed({
  get: () => props.threshold,
  set: (v) => emit('update:threshold', v),
})

const typeFilterModel = computed({
  get: () => props.typeFilter,
  set: (v) => emit('update:typeFilter', v),
})

const minDegreeModel = computed({
  get: () => props.minDegree,
  set: (v) => emit('update:minDegree', v),
})

const layoutNameModel = computed({
  get: () => props.layoutName,
  set: (v) => emit('update:layoutName', v),
})

const layoutSpacingModel = computed({
  get: () => props.layoutSpacing,
  set: (v) => emit('update:layoutSpacing', v),
})

const businessLabelPartsModel = computed({
  get: () => props.businessLabelParts,
  set: (v) => emit('update:businessLabelParts', v),
})

const personLabelPartsModel = computed({
  get: () => props.personLabelParts,
  set: (v) => emit('update:personLabelParts', v),
})

const showLabelsModel = computed({
  get: () => props.showLabels,
  set: (v) => emit('update:showLabels', v),
})

const autoLabelsByZoomModel = computed({
  get: () => props.autoLabelsByZoom,
  set: (v) => emit('update:autoLabelsByZoom', v),
})

const showIncidentsModel = computed({
  get: () => props.showIncidents,
  set: (v) => emit('update:showIncidents', v),
})

const hideIsolatesModel = computed({
  get: () => props.hideIsolates,
  set: (v) => emit('update:hideIsolates', v),
})

const showLegendModel = computed({
  get: () => props.showLegend,
  set: (v) => emit('update:showLegend', v),
})

const searchQueryModel = computed({
  get: () => props.searchQuery,
  set: (v) => emit('update:searchQuery', v),
})

const focusPidModel = computed({
  get: () => props.focusPid,
  set: (v) => emit('update:focusPid', v),
})

const zoomModel = computed({
  get: () => props.zoom,
  set: (v) => emit('update:zoom', v),
})

const focusModeModel = computed({
  get: () => props.focusMode,
  set: (v) => emit('update:focusMode', v),
})

const focusDepthModel = computed({
  get: () => props.focusDepth,
  set: (v) => emit('update:focusDepth', v),
})

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
const focusDepthFieldWidth = computed(() => widthChFromLabels([t('graph.navigate.depth1'), t('graph.navigate.depth2')], 10, 18, 4))
</script>

<template>
  <div class="toolbar">
    <el-tabs
      v-model="toolbarTabModel"
      type="card"
      class="toolbarTabs"
    >
      <el-tab-pane
        :label="t('graph.toolbar.filtersTab')"
        name="filters"
      >
        <div class="filtersGrid">
          <div class="ctl filtersGrid__eq">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.filters.equivalent')"
              tooltip-key="graph.eq"
            />
            <el-select
              v-model="eqModel"
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
            <div
              v-if="eqModel === 'ALL'"
              class="ctl__hint"
            >
              {{ t('graph.filters.equivalentNetVizHint') }}
            </div>
          </div>

          <div class="ctl filtersGrid__status">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.filters.status')"
              tooltip-key="graph.status"
            />
            <el-select
              v-model="statusFilterModel"
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

          <div class="ctl filtersGrid__threshold">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.filters.bottleneck')"
              tooltip-key="graph.threshold"
            />
            <el-input
              v-model="thresholdModel"
              size="small"
              class="ctl__field ctl__field--compact"
              :style="{ '--geo-ctl-width': thresholdFieldWidth }"
              :placeholder="t('graph.filters.bottleneckPlaceholder')"
            />
          </div>

          <div class="ctl filtersGrid__type">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.filters.type')"
              tooltip-key="graph.type"
            />
            <el-checkbox-group
              v-model="typeFilterModel"
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

          <div class="ctl filtersGrid__degree">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              :label="t('graph.filters.minDegree')"
              tooltip-key="graph.minDegree"
            />
            <el-input-number
              v-model="minDegreeModel"
              size="small"
              :min="0"
              :max="20"
              controls-position="right"
              class="ctl__field ctl__field--compact"
              :style="{ '--geo-ctl-width': minDegreeFieldWidth }"
            />
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
              v-model="layoutNameModel"
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
              v-model="layoutSpacingModel"
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
                v-model="businessLabelPartsModel"
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
                v-model="personLabelPartsModel"
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
                  v-model="showLabelsModel"
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
                  v-model="autoLabelsByZoomModel"
                  size="small"
                />
              </div>
            </div>

            <div class="displayToggleGroup">
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.incidents')"
                  tooltip-key="graph.incidents"
                />
                <el-switch
                  v-model="showIncidentsModel"
                  size="small"
                />
              </div>
              <div class="displayToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.display.hideIsolates')"
                  tooltip-key="graph.hideIsolates"
                />
                <el-switch
                  v-model="hideIsolatesModel"
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
                  v-model="showLegendModel"
                  size="small"
                />
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        :label="t('graph.toolbar.navigateTab')"
        name="navigate"
      >
        <div class="navPane">
          <GraphSearchBar
            v-model:search-query="searchQueryModel"
            v-model:focus-pid="focusPidModel"
            :can-find="canFind"
            :fetch-suggestions="fetchSuggestions"
            :on-focus-search="onFocusSearch"
          />

          <div class="navMainRow">
            <div class="navMainGroup navMainGroup--actions">
              <TooltipLabel
                class="toolbarLabel navMainGroup__label"
                :label="t('graph.navigate.actions')"
                tooltip-key="graph.actions"
                :max-lines="4"
              />
              <div class="navMainGroup__content navActions">
                <el-button
                  size="small"
                  @click="onFit"
                >
                  {{ t('graph.navigate.fit') }}
                </el-button>
                <el-button
                  size="small"
                  @click="onRelayout"
                >
                  {{ t('graph.navigate.relayout') }}
                </el-button>

                <div class="zoomrow">
                  <TooltipLabel
                    class="toolbarLabel zoomrow__label"
                    :label="t('graph.navigate.zoom')"
                    tooltip-key="graph.zoom"
                  />
                  <el-slider
                    v-model="zoomModel"
                    :min="0.1"
                    :max="3"
                    :step="0.05"
                    class="zoomrow__slider"
                  />
                </div>
              </div>
            </div>

            <div class="navMainGroup navMainGroup--focus">
              <div class="navToggle">
                <TooltipLabel
                  class="toolbarLabel"
                  :label="t('graph.navigate.focus')"
                  :tooltip-text="t('graph.navigate.focusMode.tooltip')"
                />
                <el-switch
                  v-model="focusModeModel"
                  size="small"
                />
              </div>

              <div class="navMainGroup__content navFocusControls">
                <el-select
                  v-model="focusDepthModel"
                  size="small"
                  class="ctl__field ctl__field--compact navFocus__depth"
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
                  class="navFocus__tag"
                >
                  {{ focusRootPid }}
                </el-tag>

                <el-button
                  size="small"
                  :disabled="!canUseSelectedForFocus"
                  @click="onUseSelectedForFocus"
                >
                  {{ t('graph.navigate.useSelected') }}
                </el-button>
                <el-button
                  size="small"
                  :disabled="!focusMode"
                  @click="onClearFocusMode"
                >
                  {{ t('graph.navigate.clear') }}
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>
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

.filtersGrid {
  display: grid;
  grid-template-areas: 'eq status threshold type degree';
  grid-template-columns: max-content max-content max-content max-content max-content;
  gap: 10px 12px;
  align-items: start;
  justify-content: start;
}

.filtersGrid__eq {
  grid-area: eq;
}

.filtersGrid__status {
  grid-area: status;
}

.filtersGrid__threshold {
  grid-area: threshold;
}

.filtersGrid__type {
  grid-area: type;
}

.filtersGrid__degree {
  grid-area: degree;
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



.navPane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  --geo-nav-label-w: 84px;
}

.navMainRow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
}

.navMainGroup {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.navMainGroup--focus {
  margin-left: 12px;
}

.navMainGroup__label {
  width: var(--geo-nav-label-w);
}

.navMainGroup__content {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.navToggle {
  display: flex;
  align-items: center;
  gap: 10px;
}

.navActions {
  flex-wrap: nowrap;
  width: max-content;
}

.navFocusControls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}


.navFocus__depth {
  width: var(--geo-ctl-width, 120px);
}

.navFocus__tag {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zoomrow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.zoomrow__label {
  font-size: var(--geo-font-size-label);
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.zoomrow__slider {
  flex: 0 0 auto !important;
  width: clamp(160px, 18vw, 260px) !important;
}

@media (max-width: 992px) {
  .filtersGrid {
    grid-template-areas:
      'eq status'
      'threshold type'
      'degree .';
    grid-template-columns: max-content max-content;
  }
  .navActions {
    flex-wrap: wrap;
    width: 100%;
  }

  .navMainGroup--focus {
    margin-left: 0;
  }
}

@media (max-width: 768px) {
  .filtersGrid {
    grid-template-areas:
      'eq'
      'status'
      'threshold'
      'type'
      'degree';
    grid-template-columns: 1fr;
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

  .navMainRow {
    align-items: start;
  }

  .navGroup__label {
    min-width: 0;
  }

  .zoomrow__slider {
    width: clamp(180px, 60vw, 320px);
  }
}
</style>
