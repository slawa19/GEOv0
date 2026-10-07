import { ElMessageBox } from 'element-plus'

import { t } from '../i18n'

/**
 * The one dialog that asks an operator for the reason of a state-changing action (032 S7, D-12): freeze or unfreeze
 * a participant, stop, start or delete an equivalent, clear an integrity hold. The reason goes to the audit log, so
 * it is required - a blank answer is refused inside the dialog.
 *
 * `message` is what the caller wants said before the request for a reason (for a delete: what uses the equivalent
 * and that it is permanent); `confirmKey` is the i18n key of the confirm button; `placeholderKey` the i18n key of the
 * input's hint. Resolves to the trimmed reason, or `null` when the operator cancels - never to an empty string.
 */
export async function promptReason(
  title: string,
  message: string,
  confirmKey: string,
  placeholderKey?: string,
): Promise<string | null> {
  const body = message ? `${message}\n${t('common.reasonRequired')}` : t('common.reasonRequired')
  try {
    const answer = await ElMessageBox.prompt(body, title, {
      confirmButtonText: t(confirmKey),
      cancelButtonText: t('common.cancel'),
      inputPlaceholder: placeholderKey ? t(placeholderKey) : undefined,
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : t('common.reasonIsRequired')),
      type: 'warning',
    })
    const reason = String(answer.value || '').trim()
    return reason || null
  } catch {
    return null
  }
}
