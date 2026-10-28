import { useState, useEffect } from 'react'
import { Icon, familyIcon } from './icons.jsx'
import { getToolsStatus } from './api.js'
import { useModelCatalog } from './catalog.js'
import Markdown from './Markdown.jsx'
import SpeechControls from './SpeechControls.jsx'
import { rankingOutcome } from './rankings.js'

function FinalAnswerOverlay({ answer, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6" onClick={onClose} role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Full final answer"
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-black/10 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-black/5 px-5 py-3">
          <h3 className="text-[14px] font-semibold text-ink">Final answer</h3>
          <button onClick={onClose} className="rounded-lg px-2 py-1 text-[12px] text-neutral-500 hover:bg-black/5" aria-label="Close full answer">
            ✕
          </button>
        </div>
        <div className="border-b border-black/5 px-5 py-2">
          <SpeechControls text={answer} />
        </div>
        <div className="overflow-y-auto px-5 py-4">
          <Markdown className="text-[14px] leading-relaxed text-neutral-700">{answer}</Markdown>
        </div>
      </div>
    </div>
  )
}

export default function RightPanel({ results, webSearch, onToggleWebSearch, selectedModels, onOpenTools, onRetry }) {
  const [fullFor, setFullFor] = useState(null)
  const [searchConfigured, setSearchConfigured] = useState(null)
  const { models } = useModelCatalog()
  const maxTokens = Math.max(0, ...(results?.model_responses || []).map((m) => m.tokens || 0))

  const finalAnswer = results?.final_answer || ''
  const finalError = results?.final_answer_error || null
  const pending = !!results?.streaming && !finalAnswer && !finalError
  const ranking = rankingOutcome(results?.aggregate_rankings, results?.peer_reviews)

  useEffect(() => {
    getToolsStatus()
      .then((s) => setSearchConfigured(!!s?.integrations?.web_search?.configured))
      .catch(() => setSearchConfigured(null))
  }, [])

  const searchStatus = results?.search || null
  const active = models.filter((m) => !selectedModels.length || selectedModels.includes(m.key))

  const exportAnswer = () => {
    if (!finalAnswer) return
    // Export from the structured final answer text, never from the DOM.
    const blob = new Blob([`# CogniSync Answer\n\n${finalAnswer}\n`], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'cognisync-answer.md'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const canToggleSearch = searchConfigured !== false

  return (
    <aside className="hidden h-full w-[280px] shrink-0 overflow-y-auto bg-canvas px-4 pt-5 lg:block">
      {/* The Sync */}
      <section className="rounded-2xl bg-sage p-4">
        <h2 className="font-display text-[18px] font-semibold text-ink">The Sync</h2>
        <p className="mt-0.5 text-[12px] leading-snug text-neutral-500">
          Multiple models. Independent views. A clearer answer.
        </p>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {active.map((m) => {
            const MIcon = familyIcon(m.family)
            const responded = results?.model_responses?.find((r) => r.model_id === m.key || r.model === m.name)
            return (
              <div key={m.key} className="rounded-lg bg-white/70 p-2.5">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg" style={{ background: `${m.color}18`, color: m.color }}>
                  <MIcon size={15} />
                </span>
                <div className="mt-2 flex items-center gap-1.5">
                  <span className="truncate text-[12px] font-medium text-ink" title={m.key}>{m.name}</span>
                  <span
                    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: responded === undefined ? '#22C55E88' : responded?.success === false ? '#f87171' : '#22C55E' }}
                    title={responded === undefined ? 'ready' : responded?.success === false ? 'failed' : 'responded'}
                  />
                </div>
                <div className="text-[11px] text-neutral-400">{responded?.tokens ? `${responded.tokens.toLocaleString()} tokens` : 'ready'}</div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Integrations */}
      <section className="mt-4">
        <h3 className="label-xs text-neutral-400">Integrations</h3>
        <div className="mt-2 rounded-2xl border border-black/5 bg-white p-3">
          <div className="flex items-center justify-between py-2">
            <span className="text-[13px] text-neutral-700">DuckDuckGo Search</span>
            <button
              onClick={onToggleWebSearch}
              disabled={!canToggleSearch}
              title={!canToggleSearch ? 'DuckDuckGo search is unavailable in this deployment' : 'No API key required'}
              aria-label="Toggle DuckDuckGo web search for council requests"
              className={`relative h-4 w-7 rounded-full transition-colors ${webSearch && canToggleSearch ? 'bg-gpt' : 'bg-black/10 disabled:opacity-40'}`}
            >
              <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${webSearch && canToggleSearch ? 'left-3.5' : 'left-0.5'}`} />
            </button>
          </div>
          {[{ name: 'GitHub' }, { name: 'Notion' }].map(({ name }) => (
            <div key={name} className="flex items-center justify-between border-t border-black/5 py-2">
              <span className="text-[13px] text-neutral-700">{name}</span>
              <button
                onClick={onOpenTools}
                className="label-xs text-neutral-400 underline-offset-2 hover:text-ink hover:underline"
              >
                Test / Setup
              </button>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-neutral-400">
          DuckDuckGo search needs no API key. Verify live availability from the Tools view.
        </p>
        {searchStatus?.enabled && (
          <p className={`mt-1 text-[11px] ${searchStatus.status === 'success' ? 'text-gpt' : 'text-amber-600'}`}>
            {searchStatus.status === 'success'
              ? `Grounded with ${searchStatus.result_count || 0} DuckDuckGo result${searchStatus.result_count === 1 ? '' : 's'}.`
              : `Search not used: ${searchStatus.error_message || searchStatus.status || 'unavailable'}`}
          </p>
        )}
      </section>

      {/* Final Answer */}
      <section className="mb-6 mt-4 rounded-2xl border border-black/5 bg-white p-4">
        <h3 className="text-[13px] font-semibold text-ink">Final Answer</h3>
        <p className="mt-0.5 text-[11px] leading-snug text-neutral-400">
          The chairman weighs every voice and consensus vote.
        </p>
        {ranking.winner && (
          <p className="mt-1 text-[11px] leading-snug text-neutral-500">
            Peer review: <span className="font-medium text-ink">{ranking.winner.model}</span> ranked first
            {' '}(avg rank {ranking.winner.average_rank}).
          </p>
        )}
        {ranking.tied && (
          <p className="mt-1 text-[11px] leading-snug text-neutral-500">
            Peer review is tied at average rank {ranking.leaders[0].average_rank}; no single winner is declared.
          </p>
        )}

        {/* green bar chart: tokens per model */}
        <div className="mt-3 flex h-20 items-end gap-2 border-b border-black/10 px-1 pb-0">
          {active.map((m, i) => {
            const r = results?.model_responses?.find((res) => res.model_id === m.key || res.model === m.name)
            const h = maxTokens > 0 && r?.tokens ? Math.max(8, Math.round((r.tokens / maxTokens) * 100)) : 0
            return (
              <div key={m.key} className="flex flex-1 flex-col items-center gap-1">
                <div
                  className="w-full rounded-t-sm transition-[height] duration-700 ease-out"
                  style={{ height: `${h}%`, background: `#22C55E${i === 0 ? '' : i === 1 ? 'DD' : i === 2 ? 'BB' : '99'}` }}
                  title={`${m.name}: ${r?.tokens ?? '—'} tokens`}
                />
                <span className="max-w-full truncate text-[9px] text-neutral-400">{m.name.split(/[:\- ]/)[0]}</span>
              </div>
            )
          })}
        </div>

        {pending && <p className="mt-3 text-[11px] text-neutral-400">Chairman is synthesizing the final answer…</p>}

        {finalAnswer && (
          <>
            <SpeechControls text={finalAnswer} className="mt-3" />
            <Markdown className="mt-3 max-h-64 overflow-y-auto text-[12px] leading-relaxed text-neutral-600">
              {finalAnswer}
            </Markdown>
            {results?.sources?.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 text-[11px] text-neutral-400">
                {results.sources.map((s) => (
                  <li key={s.result_id || s.url} className="truncate">
                    Source: <a href={s.url} target="_blank" rel="noreferrer" className="underline">{s.title}</a> ({s.domain})
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {!pending && !finalAnswer && finalError && (
          <div className="mt-3 rounded-lg bg-red-50 px-3 py-2">
            <p className="text-[11px] text-red-600">{finalError}</p>
            {onRetry && (
              <button onClick={onRetry} className="mt-1.5 rounded-md bg-red-500 px-2 py-1 text-[11px] text-white hover:bg-red-600">
                Retry
              </button>
            )}
          </div>
        )}

        {!pending && !finalAnswer && !finalError && (
          <p className="mt-3 text-[11px] text-neutral-400">
            {results ? 'No final answer was produced for this run.' : 'No answer yet — ask a question.'}
          </p>
        )}

        <div className="mt-3 flex gap-2">
          <button
            onClick={() => setFullFor(finalAnswer)}
            disabled={!finalAnswer}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-ink px-3 py-2 text-[12px] font-medium text-white hover:bg-black disabled:opacity-30"
          >
            View full answer <span aria-hidden="true">→</span>
          </button>
          <button
            onClick={exportAnswer}
            disabled={!finalAnswer}
            className="flex items-center gap-1.5 rounded-lg border border-black/10 px-3 py-2 text-[12px] text-neutral-600 hover:border-black/30 disabled:opacity-30"
          >
            <Icon name="download" size={12} /> Export
          </button>
        </div>
      </section>

      {fullFor && fullFor === finalAnswer && <FinalAnswerOverlay answer={fullFor} onClose={() => setFullFor(null)} />}
    </aside>
  )
}
