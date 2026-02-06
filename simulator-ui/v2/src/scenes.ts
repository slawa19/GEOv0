export const SCENES = {
  A: { id: 'A', label: 'A — Overview' },
  B: { id: 'B', label: 'B — Focus' },
  C: { id: 'C', label: 'C — Statuses' },
} as const

export type SceneId = keyof typeof SCENES

export const SCENE_IDS = Object.keys(SCENES) as SceneId[]
