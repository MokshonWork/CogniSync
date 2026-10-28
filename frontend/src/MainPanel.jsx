import { useState, useEffect, useRef } from 'react'
import { Icon, familyIcon } from './icons.jsx'
import { exposeModelCatalog, cachedModels } from './catalog.js'
import ModelCard from './ModelCard.jsx'
import ModelPerformance from './ModelPerformance.jsx'
import ModelComparison from './ModelComparison.jsx'
import DiscussionTimeline from './DiscussionTimeline.jsx'
import Markdown from './Markdown.jsx'
import SpeechControls from './SpeechControls.jsx'
import { rankingOutcome } from './rankings.js'

const CHIPS = ['Compare different approaches', 'Analyze a trade-off', 'Challenge my assumptions']

export default function MainPanel({
  conversationId,
  results,
  loading,
  onSend,
  onRetry,
  onCancel,
  focusSignal,
  defaultMode,
  selected,
  onToggleSelected,
  webSearch,
  onOpenTools,
}) {
  const [input, setInput] = useState('')
  const [mode, setMode] = useState(defaultMode || 'Balanced')
  const [showModels, setShowModels] = useState(false)
  const [models, setModels] = useState(cachedModels() || [])
  const [catalogError, setCatalogError] = useState(null)
  // Queued follow-up drafts while the council computes (FIFO, one slot per
  // accepted submission). The active prompt lives in `results`, never here.
  const [queued, setQueued] = useState([])
  const inputRef = useRef(null)
  const scrollRef = useRef(null)
  const finalRef = useRef(null)
  const submittingRef = useRef(false)
  const nearBottomRef = useRef(true)
  const userScrolledAwayRef = useRef(false)
  const scrolledForRef = useRef(null)
  const [showJump, setShowJump] = useState(false)

  useEffect(() => {
    let cancelled = false
    exposeModelCatalog()
      .then((c) => { if (!cancelled) { setModels(c.models); setCatalogError(c.models?.length ? null : 'No free models in catalog') } })
      .catch((e) => { if (!cancelled) setCatalogError(e.message) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    inputRef.current?.focus()
    const usePrompt = (e) => { setInput(e.detail); inputRef.current?.focus() }
    window.addEventListener('cognisync:use-prompt', usePrompt)
    return () => window.removeEventListener('cognisync:use-prompt', usePrompt)
  }, [focusSignal])

  const submit = (queuedText) => {
    if (showModels) setShowModels(false)
    const fromComposer = queuedText == null
    // Capture the exact text NOW; the composer is cleared immediately after,
    // and the request must use this captured value, never the cleared state.
    const question = (fromComposer ? input : queuedText).trim()
    if (!question) return
    if (loading) {
      // Queue the draft without touching the running request. Order is
      // preserved; the composer clears only once accepted here.
      setQueued((q) => (q.length && q[q.length - 1].text === question
        ? q
        : [...q, {
            id: crypto.randomUUID?.() || String(Date.now() + Math.random()),
            text: question,
            conversationId,
          }]))
      if (fromComposer) setInput('')
      return
    }
    if (submittingRef.current) return // repeated Enter/click before React re-renders
    submittingRef.current = true
    if (fromComposer) setInput('') // clear immediately — prompt is already in the transcript
    onSend({
      question,
      models: selected.filter((k) => models.some((m) => m.key === k)),
      mode,
      webSearch: !!webSearch,
    })
  }

  // Drafts belong to the conversation they were typed in. Switching
  // conversations (new session, history, or a run starting elsewhere) must
  // discard them rather than auto-sending into the wrong transcript.
  useEffect(() => {
    setQueued((all) => all.filter((item) => item.conversationId === conversationId))
  }, [conversationId])

  // Once the active request settles, dispatch the oldest queued draft — but
  // only into the conversation that queued it.
  useEffect(() => {
    if (loading) {
      submittingRef.current = false
      return
    }
    if (queued.length) {
      const [next, ...rest] = queued
      setQueued(rest)
      if (next.conversationId === conversationId) submit(next.text)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, conversationId])

  // Reset scroll intentions per run so a new question can auto-reveal again.
  useEffect(() => {
    userScrolledAwayRef.current = false
    nearBottomRef.current = true
    scrolledForRef.current = null
    setShowJump(false)
  }, [results?.question])

  const finalAnswer = results?.final_answer || ''
  const ranking = rankingOutcome(results?.aggregate_rankings, results?.peer_reviews)

  // Reveal the final answer exactly once per run, only when the user has not
  // scrolled away to read earlier content, and never on a failed run.
  useEffect(() => {
    if (!results || results.streaming || !finalAnswer) return
    if (scrolledForRef.current === results.question) return
    if (userScrolledAwayRef.current || !nearBottomRef.current) {
      setShowJump(true)
      return
    }
    scrolledForRef.current = results.question
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
    finalRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  }, [results, finalAnswer])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    nearBottomRef.current = distance < 96
    if (!nearBottomRef.current) userScrolledAwayRef.current = true
  }

  const jumpToFinal = () => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
    finalRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
    setShowJump(false)
  }

  const toggleModel = (key) => {
    onToggleSelected(key)
  }

  const hasResults = !!(results?.model_responses?.length || results?.discussion?.length || results?.question)

  const modelsDropdown = (
    <div className="absolute z-20 w-64 cursor-pointer overflow-y-auto rounded-lg border border-black/10 bg-white p-1 shadow-lg max-h-64">
      {catalogError && !models.length ? (
        <p className="px-2 py-2 text-[12px] text-red-500">{catalogError}</p>
      ) : null}
      {models.map((m) => {
        const on = selected.includes(m.key)
        const MIcon = familyIcon(m.family)
        return (
          <button
            key={m.key}
            onClick={() => toggleModel(m.key)}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-neutral-700 hover:bg-black/5"
            title={m.key}
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: m.color }} />
            <span className="flex-1 truncate">{m.name}</span>
            <span className={`h-3.5 w-3.5 rounded border ${on ? 'border-gpt bg-gpt text-white' : 'border-black/20'}`}>
              {on && <svg viewBox="0 0 10 10" width="10" height="10" fill="currentColor"><path d="M1 5l3 3 5-6l-1-1l-4 5l-2-2z" /></svg>}
            </span>
          </button>
        )
      })}
    </div>
  )

  const panelTopBar = (
    <div className="flex shrink-0 flex-wrap items-center gap-3 px-8 pt-5">
      <div className="relative">
        <button
          onClick={() => setShowModels((v) => !v)}
          className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-1.5 text-[12px] text-neutral-600 hover:border-black/20"
        >
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-gpt" />
          {results?.model_responses?.length
            ? `${results.model_responses.filter((m) => m.success).length}/${results.model_responses.length} responded`
            : `${selected.length || models.length} models selected`}
          <Icon name="chevron" size={12} />
        </button>
        {showModels && modelsDropdown}
      </div>
      <button
        onClick={() => setMode((m) => (m === 'Balanced' ? 'Fast' : 'Balanced'))}
        className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-1.5 text-[12px] text-neutral-600 hover:border-black/20"
      >
        {mode} <Icon name="chevron" size={12} />
      </button>
      <button
        onClick={onOpenTools}
        className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-1.5 text-[12px] text-neutral-600 hover:border-black/20"
      >
        <Icon name="wrench" size={12} /> Tools
      </button>
      {webSearch && (
        <span className="rounded-full bg-gpt/10 px-3 py-1 text-[12px] text-gpt">Web search on</span>
      )}
    </div>
  )

  const renderResults = () => (
    <>
      {results?.question && <p className="mt-6 text-[13px] text-neutral-400 break-words">{results.question}</p>}
      {loading && (
        <p className="mt-2 text-[13px] text-neutral-400">
          Council is responding — models answer in parallel, then review each other anonymously before the chairman synthesizes.
          {results?.reviewProgress && results.reviewProgress.total > 0 && (
            <> {results.reviewProgress.done}/{results.reviewProgress.total} peer reviews completed.</>
          )}
          {results?.failed && <> Last attempt failed — “Retry” re-sends the same prompt to the SAME conversation.</>}
        </p>
      )}
      {results?.note && <p className="mt-1 text-[12px] text-amber-600">{results.note}</p>}
      {results?.search?.enabled && (
        <p className={`mt-1 text-[12px] ${results.search.status === 'success' ? 'text-gpt' : 'text-amber-600'}`}>
          {results.search.status === 'success'
            ? 'Grounded with server-side DuckDuckGo search results.'
            : `DuckDuckGo search unavailable: ${results.search.error_message || results.search.error || results.search.status}`}
        </p>
      )}
      {results?.sources?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {results.sources.map((s) => (
            <a
              key={s.result_id}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              title={s.url}
              className="rounded-full border border-black/10 bg-white px-3 py-1 text-[11px] text-neutral-600 hover:border-black/30"
            >
              [{s.result_id}] {s.title} · {s.domain}
            </a>
          ))}
        </div>
      )}
      {results?.failed && (
        <div className="mt-3 rounded-xl border border-red-100 bg-red-50 p-4">
          <p className="text-[13px] text-red-600">
            {results.discussion?.[0]?.text || 'Request failed — the backend may be unreachable.'}
          </p>
          <div className="mt-2 flex gap-2">
            <button onClick={onRetry} className="rounded-lg bg-red-500 px-3 py-1.5 text-[12px] text-white hover:bg-red-600">Retry</button>
            {onCancel && <button onClick={onCancel} className="rounded-lg border border-black/10 px-3 py-1.5 text-[12px] text-neutral-600">Stop</button>}
            <button onClick={onOpenTools} className="rounded-lg border border-red-200 px-3 py-1.5 text-[12px] text-red-500">Check model status</button>
          </div>
        </div>
      )}
      <ModelPerformance responses={results?.model_responses || []} loading={loading} models={models} />
      <ModelComparison
        responses={results?.model_responses || []}
        aggregateRankings={results?.aggregate_rankings || []}
        peerReviews={results?.peer_reviews || []}
        criteria={results?.evaluation_criteria || []}
        loading={loading}
        models={models}
      />

      <div className="mt-4 grid gap-5 md:grid-cols-1 xl:grid-cols-2">
        <section className="min-w-0 rounded-2xl border border-black/10 bg-white p-5">
          <h2 className="text-[14px] font-semibold text-ink">Model Responses</h2>
          <div className="mt-3 flex flex-col gap-3">
            {(results?.model_responses || []).length === 0 && loading && (
              <p className="text-[12px] text-neutral-400">Resolving council models from the free catalog…</p>
            )}
            {(results?.model_responses || []).map((r) => (
              <ModelCard
                key={r.model_id || r.model}
                name={r.model}
                color={models.find((m) => m.key === r.model_id)?.color || '#9CA3AF'}
                r={r}
              />
            ))}
          </div>
        </section>

        <section className="min-w-0 rounded-2xl border border-black/10 bg-white p-5">
          <h2 className="text-[14px] font-semibold text-ink">Council Discussion</h2>
          <div className="mt-4">
            <DiscussionTimeline loading={loading} discussion={results?.discussion} reviewProgress={results?.reviewProgress} />
          </div>
        </section>
      </div>

      {results?.streaming && !finalAnswer && (
        <p className="mt-5 text-[12px] text-neutral-400">Chairman is synthesizing the final answer…</p>
      )}

      {finalAnswer && (
        <section ref={finalRef} id="final-answer" className="mt-5 scroll-mt-4 rounded-2xl border border-gpt/20 bg-gpt/[0.04] p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[14px] font-semibold text-ink">Final Answer</h2>
            <div className="flex flex-wrap items-center gap-2">
              <SpeechControls text={finalAnswer} />
              <span className="label-xs rounded-full bg-gpt/10 px-2 py-1 text-gpt">Consensus synthesis</span>
            </div>
          </div>
          <p className="mt-0.5 text-[11px] text-neutral-400">
            {results.synthesis_model ? `Synthesized by ${results.synthesis_model}` : 'Chairman synthesis'}
            {ranking.winner && ` · ${ranking.winner.model} led peer review (avg rank ${ranking.winner.average_rank})`}
            {ranking.tied && ` · Peer review tied at average rank ${ranking.leaders[0].average_rank}`}
          </p>
          <Markdown className="mt-3 max-w-[76ch] text-[14px] leading-relaxed text-neutral-700">{finalAnswer}</Markdown>
          {results?.sources?.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1 text-[11px] text-neutral-400">
              {results.sources.map((s) => (
                <li key={s.result_id || s.url} className="truncate">
                  Source: <a href={s.url} target="_blank" rel="noreferrer" className="underline">{s.title}</a>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {!results?.streaming && !finalAnswer && results?.final_answer_error && (
        <div className="mt-5 rounded-xl border border-red-100 bg-red-50 p-4">
          <p className="text-[13px] text-red-600">Synthesis failed: {results.final_answer_error}</p>
          <p className="mt-1 text-[11px] text-red-500">Successful model answers above are preserved.</p>
          <div className="mt-2 flex gap-2">
            <button onClick={onRetry} className="rounded-lg bg-red-500 px-3 py-1.5 text-[12px] text-white hover:bg-red-600">Retry</button>
            <button onClick={onOpenTools} className="rounded-lg border border-red-200 px-3 py-1.5 text-[12px] text-red-500">Check model status</button>
          </div>
        </div>
      )}
    </>
  )

  const renderHero = () => (
    <div className="mt-12 flex flex-col items-start justify-between gap-10 xl:flex-row">
      <div className="max-w-[520px] min-w-0">
        <p className="label-xs text-accent">Different perspectives. Stronger answers.</p>
        <h1 className="mt-3 font-display text-[44px] leading-[1.15] text-ink">
          Let&apos;s think this <em className="italic">through.</em>
        </h1>
        <p className="mt-3 text-[14px] text-neutral-500">
          Your question runs through every selected free model in parallel — anonymously peer-reviewed, then synthesized by the chairman.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          {CHIPS.map((c) => (
            <button
              key={c}
              onClick={() => { setInput(c); inputRef.current?.focus() }}
              title="Fills the composer — press Enter to send"
              className="rounded-full border border-black/10 bg-white px-3 py-1.5 text-[12px] text-neutral-600 hover:border-black/30"
            >
              {c}
            </button>
          ))}
        </div>
      </div>
      <div className="hero-blob hidden h-52 w-52 shrink-0 lg:block" />
    </div>
  )

  return (
    <main className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-canvas">
      {panelTopBar}

      {/* scrollable content — the single primary scroll container */}
      <div ref={scrollRef} onScroll={handleScroll} className="relative min-h-0 flex-1 overflow-y-auto px-8 pb-8">
        {hasResults || loading ? renderResults() : renderHero()}
      </div>

      {showJump && finalAnswer && (
        <div className="pointer-events-none absolute bottom-4 left-1/2 z-10 -translate-x-1/2">
          <button
            onClick={jumpToFinal}
            className="pointer-events-auto rounded-full bg-ink px-4 py-2 text-[12px] text-white shadow-lg hover:bg-black"
          >
            Jump to final answer ↓
          </button>
        </div>
      )}

      {/* persistent composer — always mounted, below the scroll area */}
      <div className="shrink-0 border-t border-black/10 bg-canvas px-8 py-4">
        {(loading || queued.length > 0) && (
          <div className="mb-2 flex flex-col gap-1 rounded-lg bg-amber-50 px-3 py-1.5">
            {loading && results?.question && (
              <div className="text-[12px] text-amber-700">
                <span className="font-medium">Running:</span> {results.question.length > 60 ? `${results.question.slice(0, 60)}…` : results.question}
              </div>
            )}
            {queued.map((q, i) => (
              <div key={q.id} className="flex items-center justify-between gap-3">
                <span className="text-[12px] text-amber-700">
                  <span className="font-medium">Queued #{i + 1}:</span> {q.text.length > 60 ? `${q.text.slice(0, 60)}…` : q.text}
                </span>
                <button
                  onClick={() => {
                    setQueued((all) => all.filter((item) => item.id !== q.id))
                    setInput((prev) => (prev ? `${prev}\n${q.text}` : q.text))
                    inputRef.current?.focus()
                  }}
                  className="shrink-0 text-[12px] text-amber-600 underline-offset-2 hover:underline"
                  aria-label={`Cancel queued message ${i + 1} and restore the draft`}
                >
                  ✕ restore
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-black/10 bg-white px-4 py-3">
          {!hasResults && !loading && (
            <div className="relative">
              <button
                onClick={() => setShowModels((v) => !v)}
                className="flex items-center gap-1.5 rounded-full bg-black/5 px-3 py-1.5 text-[12px] text-neutral-600"
              >
                + Models {selected.length}
                <Icon name="chevron" size={12} />
              </button>
              {showModels && modelsDropdown}
            </div>
          )}
          <button
            onClick={onOpenTools}
            className="hidden items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] text-neutral-600 hover:bg-black/5 md:flex"
          >
            <Icon name="wrench" size={12} /> Tools
          </button>
          <button
            onClick={() => setMode((m) => (m === 'Balanced' ? 'Fast' : 'Balanced'))}
            className="hidden items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] text-neutral-600 hover:bg-black/5 md:flex"
          >
            {mode} <Icon name="chevron" size={12} />
          </button>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (!loading && !input.trim()) return
                submit()
              }
            }}
            placeholder={loading ? 'Type your next message — it queues until the council finishes…' : hasResults ? 'Ask a follow-up…' : 'Ask CogniSync anything…'}
            rows={hasResults || loading ? 1 : 3}
            className={`min-h-[24px] flex-1 resize-y bg-transparent text-[14px] outline-none placeholder:text-neutral-400 ${hasResults ? 'max-h-32' : 'max-h-40'}`}
          />
          {queued.length > 0 && <span className="mb-1 text-[10px] text-amber-600">{queued.length} queued</span>}
          <button
            onClick={() => submit()}
            disabled={!input.trim()}
            title={loading ? 'Queues the message; does not send twice' : 'Send'}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-white transition-opacity hover:bg-black disabled:opacity-30"
            aria-label="Send"
          >
            <Icon name="arrowUp" size={15} />
          </button>
        </div>
      </div>
    </main>
  )
}
