<script setup lang="ts">
import { computed } from 'vue'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import GraphSearchBar from './GraphSearchBar.vue'

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
</script>

<template>
  <div class="toolbar">
    <el-tabs
      v-model="toolbarTabModel"
      type="card"
      class="toolbarTabs"
    >
      <el-tab-pane
        label="Filters"
        name="filters"
      >
        <div class="paneGrid">
          <div class="ctl ctl--eq">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Equivalent"
              tooltip-key="graph.eq"
            />
            <el-select
              v-model="eqModel"
              size="small"
              class="ctl__field"
              data-testid="graph-filter-eq"
            >
              <el-option
                v-for="c in availableEquivalents"
                :key="c"
                :label="c"
                :value="c"
              />
            </el-select>
          </div>

          <div class="ctl ctl--status">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Status"
              tooltip-key="graph.status"
            />
            <el-select
              v-model="statusFilterModel"
              multiple
              collapse-tags
              collapse-tags-tooltip
              size="small"
              class="ctl__field"
            >
              <el-option
                v-for="s in statuses"
                :key="s.value"
                :label="s.label"
                :value="s.value"
              />
            </el-select>
          </div>

          <div class="ctl ctl--threshold">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Bottleneck"
              tooltip-key="graph.threshold"
            />
            <el-input
              v-model="thresholdModel"
              size="small"
              class="ctl__field"
              placeholder="0.10"
            />
          </div>

          <div class="ctl">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Type"
              tooltip-key="graph.type"
            />
            <el-checkbox-group
              v-model="typeFilterModel"
              size="small"
            >
              <el-checkbox-button label="person">
                person
              </el-checkbox-button>
              <el-checkbox-button label="business">
                business
              </el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="ctl ctl--degree">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Min degree"
              tooltip-key="graph.minDegree"
            />
            <el-input-number
              v-model="minDegreeModel"
              size="small"
              :min="0"
              :max="20"
              controls-position="right"
              class="ctl__field"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        label="Display"
        name="display"
      >
        <div class="paneGrid">
          <div class="ctl ctl--layout">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Layout"
              tooltip-key="graph.layout"
            />
            <el-select
              v-model="layoutNameModel"
              size="small"
              class="ctl__field"
            >
              <el-option
                v-for="o in layoutOptions"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
          </div>

          <div class="ctl">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Layout spacing"
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

          <div class="ctl">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Business labels"
              tooltip-key="graph.labels"
            />
            <el-checkbox-group
              v-model="businessLabelPartsModel"
              size="small"
            >
              <el-checkbox-button label="name">
                name
              </el-checkbox-button>
              <el-checkbox-button label="pid">
                pid
              </el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="ctl">
            <TooltipLabel
              class="toolbarLabel ctl__label"
              label="Person labels"
              tooltip-key="graph.labels"
            />
            <el-checkbox-group
              v-model="personLabelPartsModel"
              size="small"
            >
              <el-checkbox-button label="name">
                name
              </el-checkbox-button>
              <el-checkbox-button label="pid">
                pid
              </el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="geoToggleGrid">
            <div class="geoToggleLine">
              <TooltipLabel
                class="toolbarLabel"
                label="Labels"
                tooltip-key="graph.labels"
              />
              <el-switch
                v-model="showLabelsModel"
                size="small"
              />
            </div>
            <div class="geoToggleLine">
              <TooltipLabel
                class="toolbarLabel"
                label="Auto labels"
                tooltip-key="graph.labels"
              />
              <el-switch
                v-model="autoLabelsByZoomModel"
                size="small"
              />
            </div>
            <div class="geoToggleLine">
              <TooltipLabel
                class="toolbarLabel"
                label="Incidents"
                tooltip-key="graph.incidents"
              />
              <el-switch
                v-model="showIncidentsModel"
                size="small"
              />
            </div>
            <div class="geoToggleLine">
              <TooltipLabel
                class="toolbarLabel"
                label="Hide isolates"
                tooltip-key="graph.hideIsolates"
              />
              <el-switch
                v-model="hideIsolatesModel"
                size="small"
              />
            </div>
            <div class="geoToggleLine">
              <TooltipLabel
                class="toolbarLabel"
                label="Legend"
                tooltip-key="graph.legend"
              />
              <el-switch
                v-model="showLegendModel"
                size="small"
              />
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        label="Navigate"
        name="navigate"
      >
        <div class="navPane">
          <GraphSearchBar
            v-model:search-query="searchQueryModel"
            v-model:focus-pid="focusPidModel"
            :fetch-suggestions="fetchSuggestions"
            :on-focus-search="onFocusSearch"
          />

          <div class="navRow navRow--actions">
            <TooltipLabel
              class="toolbarLabel navRow__label"
              label="Actions"
              tooltip-key="graph.actions"
            />
            <div class="navActions">
              <el-button
                size="small"
                :disabled="!canFind"
                @click="onFocusSearch"
              >
                Find
              </el-button>
              <el-button
                size="small"
                @click="onFit"
              >
                Fit
              </el-button>
              <el-button
                size="small"
                @click="onRelayout"
              >
                Re-layout
              </el-button>

              <div class="zoomrow">
                <TooltipLabel
                  class="toolbarLabel zoomrow__label"
                  label="Zoom"
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

          <div class="navRow navRow--focus">
            <TooltipLabel
              class="toolbarLabel navRow__label"
              label="Focus"
              tooltip-text="Focus Mode shows a small ego-subgraph (depth 1–2) around a participant to reduce noise."
            />
            <div class="navFocus">
              <el-switch
                v-model="focusModeModel"
                size="small"
              />
              <el-select
                v-model="focusDepthModel"
                size="small"
                class="navFocus__depth"
                :disabled="!focusMode"
              >
                <el-option
                  label="Depth 1"
                  :value="1"
                />
                <el-option
                  label="Depth 2"
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
                Use selected
              </el-button>
              <el-button
                size="small"
                :disabled="!focusMode"
                @click="onClearFocusMode"
              >
                Clear
              </el-button>
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

.paneGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px 12px;
  align-items: start;
}

.paneGrid > .geoToggleGrid {
  grid-column: 1 / -1;
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
}

.sliderField {
  width: 100%;
  min-width: 220px;
}

.ctl--eq {
  min-width: 160px;
}

.ctl--status {
  min-width: 220px;
}

.ctl--threshold {
  min-width: 160px;
}

.ctl--degree {
  min-width: 160px;
}

.ctl--layout {
  min-width: 200px;
}



.navPane {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.navRow {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
}

.navRow__label {
  min-width: 76px;
}

.navRow__field :deep(.el-autocomplete),
.navRow__field :deep(.el-input),
.navRow__field :deep(.el-input__wrapper) {
  width: 100%;
}

.navActions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.navFocus {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.navFocus__depth {
  width: 120px;
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
}

.zoomrow__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.zoomrow__slider {
  width: clamp(160px, 18vw, 260px);
}

@media (max-width: 992px) {
  .paneGrid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }

  .sliderField {
    min-width: 200px;
  }
}

@media (max-width: 768px) {
  .navRow {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .navRow__label {
    min-width: 0;
  }

  .zoomrow__slider {
    width: clamp(180px, 60vw, 320px);
  }
}
</style>
