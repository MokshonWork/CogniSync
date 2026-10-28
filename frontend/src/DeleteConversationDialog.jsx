import { useEffect, useRef } from 'react'

export default function DeleteConversationDialog({ conversation, busy, error, onCancel, onConfirm }) {
  const cancelRef = useRef(null)
  const confirmRef = useRef(null)
  const open = !!conversation

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement
    cancelRef.current?.focus()
    return () => previous?.focus?.()
  }, [open, conversation?.id])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onCancel])

  if (!open) return null

  const trapFocus = (e) => {
    if (e.key !== 'Tab') return
    const cancel = cancelRef.current
    const confirm = confirmRef.current
    if (!cancel || !confirm) return
    if (e.shiftKey && document.activeElement === cancel) {
      e.preventDefault()
      confirm.focus()
    } else if (!e.shiftKey && document.activeElement === confirm) {
      e.preventDefault()
      cancel.focus()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6"
      role="presentation"
      onClick={busy ? undefined : onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-conversation-title"
        aria-describedby="delete-conversation-description"
        aria-busy={busy}
        className="w-full max-w-md rounded-2xl border border-black/10 bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapFocus}
      >
        <h2 id="delete-conversation-title" className="font-display text-[20px] font-semibold text-ink">
          Delete conversation?
        </h2>
        <p id="delete-conversation-description" className="mt-2 text-[13px] leading-relaxed text-neutral-500">
          “{conversation.title || 'New Conversation'}” and every message in it will be permanently deleted.
          This cannot be undone.
        </p>

        {error && (
          <p role="alert" className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-600">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-black/10 px-3 py-2 text-[12px] text-neutral-600 hover:border-black/30 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded-lg bg-red-600 px-3 py-2 text-[12px] font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {busy ? 'Deleting…' : 'Delete conversation'}
          </button>
        </div>
      </div>
    </div>
  )
}
