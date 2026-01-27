export const SCENES = {
  A: { id: 'A', label: 'A — Overview' },
  B: { id: 'B', label: 'B — Focus' },
  C: { id: 'C', label: 'C — Statuses' },
  D: { id: 'D', label: 'D — Tx burst', requiresEvents: true, eventPlaylist: 'demo-tx' },
  E: { id: 'E', label: 'E — Clearing', requiresEvents: true, eventPlaylist: 'demo-clearing' },
} as const

export type SceneId = keyof typeof SCENES

export const SCENE_IDS = Object.keys(SCENES) as SceneId[]

export function isDemoScene(scene: SceneId) {
  return scene === 'D' || scene === 'E'
}
