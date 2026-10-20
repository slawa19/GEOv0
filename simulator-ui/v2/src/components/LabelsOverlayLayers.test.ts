/**
 * 039 slice B, PR 1: a participant's NAME is text of the theme, not a data colour. The component sets no colour on
 * a name label; the colour is the stylesheet's (`--ds-text-1`, with the halo).
 * NOT seen here: the stylesheet and what a browser computes (`e2e/qa/data-colours.qa.ts` reads the computed colour in
 * both themes; the CSS-text guard of App.css was removed by 041 T4107, F-041-9).
 */
import { createApp, h } from 'vue'
import { describe, expect, it } from 'vitest'

import LabelsOverlayLayers from './LabelsOverlayLayers.vue'

describe('LabelsOverlayLayers - name labels (039 slice B)', () => {
  it('a name label carries its text and NO inline colour, whatever else is passed in', () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({
      render: () => h(LabelsOverlayLayers, {
        // A stale caller that still sends a colour must not be able to paint the name with it.
        labelNodes: [{ id: 'a', x: 1, y: 2, text: 'Alice', color: '#ff0000' } as { id: string; x: number; y: number; text: string }],
        floatingLabels: [],
        worldToCssTranslateNoScale: (x: number, y: number) => `translate(${x}px, ${y}px)`,
      }),
    })
    app.mount(host)
    const label = host.querySelector<HTMLElement>('.node-label-inner')!
    expect(label.textContent).toBe('Alice')
    expect(label.getAttribute('style'), 'no inline style on the name').toBeNull()
    expect(host.innerHTML).not.toContain('#ff0000')
    app.unmount()
    host.remove()
  })
})
