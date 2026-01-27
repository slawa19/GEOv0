import type { GraphLink, GraphNode } from '../types'

export type LayoutNode = GraphNode & { __x: number; __y: number }
export type LayoutLink = GraphLink & { __key: string }

export type LayoutNodeLike = Pick<LayoutNode, '__x' | '__y'>
export type LayoutNodeWithId = LayoutNodeLike & { id: string }
export type LayoutLinkLike = Pick<LayoutLink, '__key' | 'source' | 'target'>
