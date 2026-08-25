import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import NodeCardOverlay from './NodeCardOverlay.vue'
import type { GraphNode, GraphSnapshot } from '../types'

/**
 * RT-012-5 (F-012-6) — reproducer for the node-card balance on the fixtures we ship.
 *
 * `NodeCardOverlay.vue:72-86` (`netText`) prefers the major-unit `net_balance` and, when it
 * is absent, prints `net_balance_atoms` as it is. The shipped `snapshot.json` carries
 * `net_balance` on 0 of 100 nodes (asserted below, so the premise stays measured rather
 * than remembered), which means the default mode always takes the atoms branch and the
 * card shows an amount that is 10^precision times too large.
 *
 * `_meta/README.txt` names UAH the active equivalent for this build, and
 * `seeds/equivalents.json` gives UAH precision 2 and HOUR precision 1.
 */

/** Precision as `seeds/equivalents.json` declares it for the shipped fixture equivalents. */
const PRECISION_BY_EQUIVALENT: Record<string, number> = {
  UAH: 2,
  HOUR: 1,
}

function loadShippedSnapshot(equivalent: string): GraphSnapshot {
  // Anchored on this file, not on `process.cwd()`: the fixture must be found however vitest is
  // invoked. With a cwd-relative path the two premise assertions below silently fail from any
  // other working directory, and a premise that fails for the wrong reason is worse than none.
  const here = dirname(fileURLToPath(import.meta.url))
  const path = resolve(here, '../../public/simulator-fixtures/v1', equivalent, 'snapshot.json')
  return JSON.parse(readFileSync(path, 'utf8')) as GraphSnapshot
}

/** The sign rule `netText` itself applies, kept identical so only the scaling is under test. */
function signedAtoms(node: GraphNode): string {
  const raw = String(node.net_balance_atoms)
  if (raw.startsWith('-')) return raw
  if (node.net_sign === -1) return `-${raw}`
  if (node.net_sign === 0) return '0'
  return raw
}

/** Exact atoms -> major units, decimal string in, decimal string out, no float involved. */
function atomsToMajor(atoms: string, precision: number): string {
  const negative = atoms.startsWith('-')
  const digits = (negative ? atoms.slice(1) : atoms).padStart(precision + 1, '0')
  const split = digits.length - precision
  const major = precision === 0 ? digits : `${digits.slice(0, split)}.${digits.slice(split)}`
  return negative && /[1-9]/.test(digits) ? `-${major}` : major
}

function renderBalance(node: GraphNode, equivalentText: string): string {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const component: Component = NodeCardOverlay
  const app = createApp({
    render: () =>
      h(component, {
        node,
        style: { left: '0px', top: '0px' },
        edgeStats: { outLimitText: '0', inLimitText: '0', degree: 0 },
        equivalentText,

        showPinActions: false,
        isPinned: false,
        pin: () => undefined,
        unpin: () => undefined,

        interactMode: false,
        interactTrustlines: [],
        trustlinesLoading: false,
        interactBusy: false,
      }),
  })
  app.mount(host)

  const text = String(host.querySelector('.ds-ov-node-card__balance')?.textContent ?? '').trim()

  app.unmount()
  host.remove()
  return text
}

function firstNonZeroNode(snapshot: GraphSnapshot): GraphNode {
  const node = snapshot.nodes.find((n) => /[1-9]/.test(String(n.net_balance_atoms ?? '')))
  if (!node) throw new Error('shipped fixture has no node with a non-zero balance')
  return node
}

describe('RT-012-5: node-card balance on the shipped simulator fixtures', () => {
  it.each(Object.keys(PRECISION_BY_EQUIVALENT))(
    'the %s fixture supplies no major-unit net_balance, so the card must scale the atoms itself',
    (equivalent) => {
      const snapshot = loadShippedSnapshot(equivalent)
      const withMajor = snapshot.nodes.filter(
        (n) => n.net_balance != null && String(n.net_balance).trim() !== '',
      )

      expect(
        withMajor.length,
        `Premise of this reproducer: the shipped ${equivalent} snapshot has `
          + `${withMajor.length} of ${snapshot.nodes.length} nodes carrying a major-unit `
          + '`net_balance`. If this stops being 0 the default mode no longer takes the atoms '
          + 'branch and the reproducer must be re-derived, not relaxed.',
      ).toBe(0)
    },
  )

  it.each(Object.keys(PRECISION_BY_EQUIVALENT))(
    'shows a %s balance in major units, not in atoms',
    (equivalent) => {
      const precision = PRECISION_BY_EQUIVALENT[equivalent]
      const node = firstNonZeroNode(loadShippedSnapshot(equivalent))
      const atoms = signedAtoms(node)
      const expected = atomsToMajor(atoms, precision)

      const rendered = renderBalance(node, equivalent)

      expect(
        rendered,
        `Node ${node.id} holds ${atoms} atoms in the shipped ${equivalent} fixture. ${equivalent} `
          + `declares precision ${precision}, so the operator must read "${expected}"; the card shows `
          + `"${rendered}", i.e. the balance on screen is 10^${precision} times the real one.`,
      ).toBe(expected)
    },
  )

  it('reacts to the equivalent: one balance must not render identically under UAH and HOUR', () => {
    const node = firstNonZeroNode(loadShippedSnapshot('UAH'))
    const atoms = signedAtoms(node)

    const asUah = renderBalance(node, 'UAH')
    const asHour = renderBalance(node, 'HOUR')

    expect(
      atomsToMajor(atoms, PRECISION_BY_EQUIVALENT.UAH),
      'Counter-check premise: the same atoms must mean different money under UAH and HOUR, '
        + 'otherwise this case proves nothing.',
    ).not.toBe(atomsToMajor(atoms, PRECISION_BY_EQUIVALENT.HOUR))

    expect(
      asUah,
      `${atoms} atoms render as "${asUah}" under UAH (precision ${PRECISION_BY_EQUIVALENT.UAH}) and as `
        + `"${asHour}" under HOUR (precision ${PRECISION_BY_EQUIVALENT.HOUR}). Identical output means `
        + 'the card ignores the equivalent it is labelled with, so the number the operator reads is '
        + 'not an amount of anything.',
    ).not.toBe(asHour)
  })
})
