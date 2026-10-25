import { useState, useEffect } from 'react'
import { Icon } from '../icons.jsx'
import { listConversations, api } from '../api.js'
import Markdown from '../Markdown.jsx'
import { formatTimestamp } from '../time.js'

function userText(content) {
  try {
    const parsed = JSON.parse(content)
    if (parsed && typeof parsed === 'object' && typeof parsed.content === 'string') return parsed.content
  } catch {
    /* plain text message */
  }
  return content || ''
}

// Loaded lazily, only for an expanded entry. Keyed by conversation id so
// switching expansion never mixes messages between sessions.
export function ConversationDetails({ id }) {
  const [conv, setConv] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.getConversation(id)
      .then((data) => { if (!cancelled) setConv(data) })
      .catch(() => { if (!cancelled) setError('Could not load this conversation.') })
    return () => { cancelled = true }
  }, [id])

  if (error) return <p className="px-3 py-2 text-[13px] text-red-500">{error}</p>
  if (!conv) return <p className="px-3 py-2 text-[13px] text-neutral-400">Loading conversation…</p>
  if (!conv.messages?.length) return <p className="px-3 py-2 text-[13px] text-neutral-400">This conversation has no messages.</p>

  return (
    <div className="border-t border-black/5 px-3 py-3">
      {conv.messages.map((m, i) => {
        if (m.role === 'user') {
          return (
            <div key={i} className="mt-2 first:mt-0 rounded-lg bg-black/5 px-3 py-2 text-[13px] text-ink">
              <span className="label-xs mr-2 text-neutral-400">You</span>{userText(m.content)}
            </div>
          )
        }

        const finalAnswer = m.final_answer || (m.stage3?.success !== false ? m.stage3?.response : null)
        const finalError = m.final_answer_error || (m.stage3?.success === false ? m.stage3?.error : null)
        const saved = (m.stage1 || []).filter((s) => s.success)

        return (
          <div key={i} className="mt-2 rounded-lg border border-black/5 p-3">
            <div className="flex items-center gap-2">
              <span className="label-xs text-neutral-400">Final answer</span>
              {m.stage3?.model && <span className="text-[11px] text-neutral-400">{m.stage3.model}</span>}
            </div>
            {finalAnswer
              ? <Markdown className="mt-1 text-[13px] text-neutral-700">{finalAnswer}</Markdown>
              : <p className="mt-1 text-[12px] text-red-500">{finalError || 'No final answer was saved for this turn.'}</p>}

            {m.sources?.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 text-[11px] text-neutral-400">
                {m.sources.map((s) => (
                  <li key={s.result_id || s.url} className="truncate">
                    <a href={s.url} target="_blank" rel="noreferrer" className="underline">{s.title}</a>
                  </li>
                ))}
              </ul>
            )}

            {saved.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] text-neutral-400">Model responses ({saved.length})</summary>
                <div className="mt-2 flex flex-col gap-2">
                  {saved.map((s, j) => (
                    <div key={j}>
                      <div className="text-[11px] font-medium text-ink">{s.model}</div>
                      <Markdown className="mt-0.5 text-[12px] text-neutral-500">{s.response}</Markdown>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function HistoryView({ onContinue }) {
  const [conversations, setConversations] = useState(null) // null = loading
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [expandedIds, setExpandedIds] = useState(() => new Set())

  const refresh = async () => {
    try {
      const list = await listConversations()
      setError(null)
      setConversations(list)
    } catch {
      setError('Could not load conversation history. Is the backend running?')
    }
  }

  useEffect(() => {
    let cancelled = false
    listConversations()
      .then((list) => { if (!cancelled) { setError(null); setConversations(list) } })
      .catch(() => { if (!cancelled) setError('Could not load conversation history. Is the backend running?') })
    return () => { cancelled = true }
  }, [])

  const toggle = (id) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const filtered = (conversations || []).filter((c) =>
    (c.title || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <section className="mx-auto max-w-3xl px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-[28px] font-semibold text-ink">History</h1>
          <p className="text-[13px] text-neutral-400">Conversations persisted on the server.</p>
        </div>
        <button onClick={refresh} className="flex items-center gap-1.5 rounded-lg border border-black/10 bg-white px-3 py-2 text-[12px] text-neutral-600 hover:border-black/30">
          <Icon name="history" size={13} /> Refresh
        </button>
      </div>

      <div className="relative mt-5">
        <Icon name="search" size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search conversations..."
          className="w-full rounded-xl border border-black/10 bg-white py-2.5 pl-9 pr-3 text-[14px] outline-none focus:border-black/30"
        />
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-black/10 bg-white p-4">
          <p className="text-[13px] text-red-500">{error}</p>
          <button onClick={refresh} className="mt-2 rounded-lg bg-ink px-3 py-1.5 text-[12px] text-white">Retry</button>
        </div>
      )}
      {!error && conversations === null && <p className="mt-4 text-[13px] text-neutral-400">Loading…</p>}
      {!error && conversations !== null && filtered.length === 0 && (
        <div className="mt-4 rounded-xl border border-black/10 bg-white p-6 text-center">
          <p className="text-[13px] text-neutral-500">
            {conversations.length === 0 ? 'No conversations yet — send a question from the workspace.' : 'No conversations match your search.'}
          </p>
        </div>
      )}

      {!error && filtered.length > 0 && (
        <ul className="mt-4 flex flex-col gap-2">
          {filtered.map((c) => {
            const isOpen = expandedIds.has(c.id)
            const detailsId = `history-details-${c.id}`
            return (
              <li key={c.id} className="rounded-xl border border-black/10 bg-white">
                <div className="flex items-center gap-2 px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => toggle(c.id)}
                    aria-expanded={isOpen}
                    aria-controls={detailsId}
                    className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-black/[0.03]"
                  >
                    <span className={`text-neutral-400 transition-transform ${isOpen ? 'rotate-90' : ''}`} aria-hidden="true">
                      <Icon name="chevron" size={12} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-ink">{c.title || 'New Conversation'}</span>
                      <span className="block text-[11px] text-neutral-400">
                        {formatTimestamp(c.updated_at || c.created_at)}
                        {c.message_count ? ` · ${c.message_count} message${c.message_count === 1 ? '' : 's'}` : ' · No messages'}
                      </span>
                    </span>
                  </button>
                  {c.message_count > 0 && onContinue && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onContinue(c.id) }}
                      className="shrink-0 rounded-lg border border-black/10 px-3 py-1.5 text-[12px] text-neutral-600 hover:border-black/30"
                    >
                      Continue in workspace
                    </button>
                  )}
                </div>
                {isOpen && (
                  <div id={detailsId}>
                    <ConversationDetails id={c.id} />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
