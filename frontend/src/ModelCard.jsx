const STATE_CHIP = {
  queued: { label: 'queued', cls: 'bg-black/5 text-neutral-400' },
  running: { label: 'running', cls: 'bg-amber-500/10 text-amber-600' },
  completed: null, // replaced by provider-colored 'done' chip below
  failed: { label: 'failed', cls: 'bg-red-500/10 text-red-500' },
}

export default function ModelCard({ name, color, r }) {
  // r: {model, model_id, time_ms, tokens, text, success, error, state}
  const state = r?.state || (r?.success == null ? 'running' : r.success ? 'completed' : 'failed')
  const failed = state === 'failed'
  const secs = r?.time_ms ? Math.max(1, Math.round(r.time_ms / 1000)) : 0
  const pct = state === 'completed' && r?.tokens ? Math.min(100, Math.max(6, Math.round((r.tokens / 800) * 100))) : 0
  const chip = STATE_CHIP[state]

  return (
    <div className="rounded-xl border border-black/5 p-3">
      <div className="flex items-center gap-2.5">
        <span
          className={`flex h-7 w-7 items-center justify-center rounded-lg ${state === 'running' ? 'animate-pulse' : ''}`}
          style={{ background: `${color}18`, color }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v10M7 12h10" /></svg>
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-[13px] font-medium text-ink">
            <span className="truncate" title={r?.model_id || name}>{name}</span>
            {chip && <span className={`label-xs shrink-0 rounded-full px-2 py-0.5 ${chip.cls}`}>{chip.label}</span>}
          </div>
          <div className="text-[11px] text-neutral-400">
            {state === 'queued' && 'Queued — waiting for a slot…'}
            {state === 'running' && 'Waiting for the model…'}
            {failed && (r?.fallback_used ? '' : (r?.error || 'Failed to respond'))}
            {state === 'completed' && `${secs}s · ${r.tokens.toLocaleString()} tokens`}
          </div>
          {failed && r?.fallback_used && (
            <div className="text-[11px] text-amber-600">
              Rate-limited — retried via {r.fallback_display || r.fallback_used}, which also failed. {r.error_original || ''}
            </div>
          )}
        </div>
        {state === 'completed' && (
          <span className="label-xs shrink-0 rounded-full px-2 py-0.5" style={{ background: `${color}14`, color }}>done</span>
        )}
      </div>
      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-black/5">
        {state === 'completed' ? (
          <div
            className="h-full rounded-full transition-[width] duration-700 ease-out"
            style={{ width: `${pct}%`, background: color }}
          />
        ) : (
          <div className={`h-full w-1/3 rounded-full ${state === 'failed' ? 'bg-red-400/50' : 'animate-pulse'}`} />
        )}
      </div>
      {state === 'completed' && r?.text && (
        <p className="markdown-content mt-2.5 text-[12px] leading-relaxed whitespace-pre-wrap text-neutral-500 w-full">
          {r.text.slice(0, 220)}{r.text.length > 220 ? '…' : ''}
        </p>
      )}
      {failed && r?.text && (
        <p className="markdown-content mt-2.5 text-[12px] whitespace-pre-wrap text-neutral-400 w-full">{r.text}</p>
      )}
    </div>
  )
}
