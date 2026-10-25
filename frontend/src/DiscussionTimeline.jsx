import Markdown from './Markdown.jsx'

const DOT_COLORS = {
  Agreement: '#22C55E',
  'Different view': '#FF6B4A',
  'Additional insight': '#4A90D9',
  Synthesis: '#1A1A1A',
}

const STEPS = Object.keys(DOT_COLORS)

// Waiting-state lines describe what HASN'T happened yet — they never imply
// a review already occurred.
const WAITING = {
  Agreement: 'Stage 1 running — waiting for model answers.',
  'Different view': 'Peer review has not started yet.',
  'Additional insight': 'No reviewer excerpts yet.',
  Synthesis: 'Chairman synthesis has not started.',
}

export default function DiscussionTimeline({ loading, discussion, reviewProgress }) {
  const reviewNote = reviewProgress
    ? `${reviewProgress.done}/${reviewProgress.total} peer reviews completed${reviewProgress.lastError ? ' (some reviews failed)' : ''}${reviewProgress.finished ? ' — rankings aggregated.' : '.'}`
    : null

  if (!discussion?.length) {
    if (!loading) return <p className="py-4 text-[13px] text-neutral-400">The council will discuss once a model responds.</p>
    return (
      <div className="flex flex-col gap-5 py-2">
        <div className="flex items-start gap-3">
          <span className="mt-1 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-black/10" />
          <div>
            <div className="text-[12px] font-semibold text-ink">Council progress</div>
            <div className="text-[12px] text-neutral-400">{reviewNote || 'Resolving council models…'}</div>
          </div>
        </div>
        {STEPS.map((t) => (
          <div key={t} className="flex items-start gap-3 opacity-60">
            <span className="mt-1 h-2 w-2 shrink-0 rounded-full border" style={{ borderColor: `${DOT_COLORS[t]}66` }} />
            <div>
              <div className="text-[12px] font-semibold text-ink">{t}</div>
              <div className="text-[12px] text-neutral-400">{WAITING[t]}</div>
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 py-2">
      {discussion.slice(0, STEPS.length).map((d, i) => (
        <div key={i} className="flex items-start gap-3">
          <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: DOT_COLORS[d.type] || '#999' }} />
          <div>
            <div className="text-[12px] font-semibold text-ink">{d.type}</div>
            <Markdown className="mt-0.5 text-[12px] leading-relaxed text-neutral-500">{d.text}</Markdown>
          </div>
        </div>
      ))}
    </div>
  )
}
