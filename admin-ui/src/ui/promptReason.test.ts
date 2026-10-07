import { ElMessageBox } from 'element-plus'
import { describe, expect, it, vi } from 'vitest'

import { promptReason } from './promptReason'
import { t } from '../i18n'

/**
 * 032 S7 (D-12): the one dialog that asks an operator for the reason of a state-changing action. What it must keep
 * from the four copies it replaces: the reason is required (an empty or blank answer is refused in the dialog),
 * the caller's words (title, message, confirm button, placeholder) are localized keys or text it passes in, and a
 * cancelled dialog is `null`, never an empty string that a caller could mistake for a reason.
 */

function prompt() {
  return vi.spyOn(ElMessageBox, 'prompt')
}

describe('promptReason', () => {
  it('returns the reason, trimmed, and asks with the caller\'s title and confirm button', async () => {
    const spy = prompt().mockResolvedValue({ value: '  audit trail  ', action: 'confirm' } as never)

    const reason = await promptReason('Freeze P1', '', 'common.confirm', 'participant.prompt.reasonPlaceholder')

    expect(reason).toBe('audit trail')
    const [message, title, options] = spy.mock.calls[0] as [string, string, Record<string, unknown>]
    expect(title).toBe('Freeze P1')
    expect(message).toBe(t('common.reasonRequired'))
    expect(options.confirmButtonText).toBe(t('common.confirm'))
    expect(options.cancelButtonText).toBe(t('common.cancel'))
    expect(options.inputPlaceholder).toBe(t('participant.prompt.reasonPlaceholder'))
  })

  it('puts the caller\'s message before the request for a reason', async () => {
    const spy = prompt().mockResolvedValue({ value: 'x', action: 'confirm' } as never)

    await promptReason('Delete UAH', 'Used by 2 trustlines.\nThis is permanent.', 'common.delete')

    expect(String((spy.mock.calls[0] as unknown[])[0])).toBe(`Used by 2 trustlines.\nThis is permanent.\n${t('common.reasonRequired')}`)
  })

  it('refuses an empty or blank reason inside the dialog', async () => {
    const spy = prompt().mockResolvedValue({ value: 'x', action: 'confirm' } as never)
    await promptReason('T', '', 'common.confirm')
    const options = (spy.mock.calls[0] as unknown[])[2] as { inputValidator: (v: string | null | undefined) => true | string }

    expect(options.inputValidator('')).toBe(t('common.reasonIsRequired'))
    expect(options.inputValidator('   ')).toBe(t('common.reasonIsRequired'))
    expect(options.inputValidator(null)).toBe(t('common.reasonIsRequired'))
    expect(options.inputValidator(' ok ')).toBe(true)
  })

  it('is null when the operator cancels', async () => {
    prompt().mockRejectedValue('cancel')
    expect(await promptReason('T', '', 'common.confirm')).toBeNull()
  })
})
