import { MODELS, MODEL_ICONS } from '../icons.jsx'

export default function CompareView({ results, onNewRequest }) {
  const responses = (results?.model_responses || []).filter((r) => r.text)

  return (
    <section className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="font-display text-[28px] font-semibold text-ink">Comparisons</h1>
      <p className="text-[13px] text-neutral-400">Read every model's full answer side by side from your latest request.</p>

      {responses.length === 0 ? (
        <div className="mt-6 rounded-2xl border border-black/10 bg-white p-10 text-center">
          <p className="text-[14px] text-neutral-500">No council responses to compare yet.</p>
          <button onClick={onNewRequest} className="mt-3 rounded-lg bg-ink px-4 py-2 text-[13px] font-medium text-white">
            Go to workspace
          </button>
          <p className="mt-3 text-[12px] text-neutral-400">Run a question from the composer — each model's answer appears here with duration and token usage.</p>
        </div>
      ) : (
        <>
          {results.question && <p className="mt-4 text-[13px] text-neutral-400 break-words">“{results.question}”</p>}
          <div className={`mt-4 grid gap-4 ${responses.length > 2 ? 'lg:grid-cols-3 md:grid-cols-2' : 'md:grid-cols-2'}`}>
            {responses.map((r) => {
              const meta = MODELS.find((m) => m.name === r.model)
              const color = meta?.color || '#8a8a80'
              const MIcon = meta ? MODEL_ICONS[meta.key] : null
              return (
                <div key={r.model} className="flex min-w-0 flex-col rounded-2xl border border-black/10 bg-white p-4">
                  <div className="flex items-center gap-2.5">
                    {MIcon && (
                      <span className="flex h-7 w-7 items-center justify-center rounded-lg" style={{ background: `${color}18`, color }}>
                        <MIcon size={15} />
                      </span>
                    )}
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-ink">{r.model}</div>
                      <div className="text-[11px] text-neutral-400">
                        {(r.time_ms / 1000).toFixed(1)}s · {r.tokens.toLocaleString()} tokens
                      </div>
                    </div>
                  </div>
                  <p className="markdown-content mt-3 flex-1 text-[12px] leading-relaxed whitespace-pre-wrap text-neutral-600">
                    {r.text}
                  </p>
                </div>
              )
            })}
          </div>
          {(results?.discussion || []).length > 0 && (
            <div className="mt-6 rounded-2xl border border-black/10 bg-white p-5">
              <h2 className="text-[14px] font-semibold text-ink">Council agreed on</h2>
              <div className="mt-2 flex flex-col gap-1.5">
                {(results.discussion || []).slice(0, 2).map((d, i) => (
                  <p key={i} className="text-[12px] text-neutral-500"><span className="font-medium text-ink">{d.type}:</span> {d.text}</p>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-neutral-400">Derived from real peer-review ranking data, not scores.</p>
            </div>
          )}
        </>
      )}
    </section>
  )
}
