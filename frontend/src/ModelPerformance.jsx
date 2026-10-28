import { modelStatus } from './modelStatus.js'

const STATUS_CLASS = {
  Completed: 'bg-gpt/10 text-gpt',
  Failed: 'bg-red-500/10 text-red-600',
  'Timed out': 'bg-amber-500/10 text-amber-700',
  Incomplete: 'bg-amber-500/10 text-amber-700',
  Running: 'bg-amber-500/10 text-amber-600',
}

function positiveNumber(value) {
  return Number.isFinite(value) && value > 0 ? value : null
}

function formatDuration(ms) {
  if (ms == null) return 'N/A'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

function formatTokens(tokens) {
  return tokens == null ? 'N/A' : tokens.toLocaleString()
}

export default function ModelPerformance({ responses = [], loading = false, models = [] }) {
  const rows = responses.map((r) => ({
    key: r.model_id || r.model,
    name: r.model || r.model_id || 'Unknown model',
    modelId: r.model_id || r.model,
    fallback: r.fallback_display || r.fallback_used || null,
    timeMs: positiveNumber(r.time_ms),
    outputTokens: Number.isFinite(r.output_tokens) ? r.output_tokens : null,
    status: modelStatus(r),
    color: models.find((m) => m.key === r.model_id)?.color || '#9CA3AF',
  }))
  const maxTime = Math.max(0, ...rows.map((r) => r.timeMs || 0))

  return (
    <section
      aria-labelledby="model-performance-title"
      className="mt-4 rounded-2xl border border-black/10 bg-white p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="model-performance-title" className="text-[14px] font-semibold text-ink">Model Performance</h2>
          <p className="mt-0.5 text-[11px] text-neutral-400">
            Measured response time and output tokens for this run. These are observations, not a ranking.
          </p>
        </div>
        <span className="label-xs rounded-full border border-black/10 px-2 py-1 text-neutral-400">This run</span>
      </div>

      {rows.length === 0 && (
        <p className="mt-4 rounded-xl bg-black/[0.03] px-3 py-4 text-[12px] text-neutral-400" role="status">
          {loading ? 'Waiting for model results…' : 'No model-level metrics were saved for this run.'}
        </p>
      )}

      {rows.length > 0 && (
        <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div>
            <h3 className="label-xs text-neutral-400">Response time</h3>
            <ol className="mt-3 flex flex-col gap-3">
              {rows.map((r) => (
                <li key={r.key}>
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: r.color }} aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate text-neutral-600" title={r.modelId}>{r.name}</span>
                    <span className="shrink-0 tabular-nums text-neutral-500">{formatDuration(r.timeMs)}</span>
                  </div>
                  <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-black/5" aria-hidden="true">
                    {r.status === 'Running' ? (
                      <div className="h-full w-1/3 animate-pulse rounded-full bg-amber-300" />
                    ) : r.timeMs != null ? (
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${maxTime ? Math.max(3, Math.round((r.timeMs / maxTime) * 100)) : 3}%`,
                          background: r.color,
                          opacity: r.status === 'Completed' ? 1 : 0.45,
                        }}
                      />
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </div>

          <div className="min-w-0 overflow-x-auto">
            <table className="w-full min-w-[360px] text-left text-[12px]">
              <caption className="sr-only">
                Model response time, output tokens, and completion status for this run. Values are not a ranking.
              </caption>
              <thead>
                <tr className="border-b border-black/5 text-[10px] uppercase tracking-[0.08em] text-neutral-400">
                  <th scope="col" className="pb-2 pr-3 font-medium">Model</th>
                  <th scope="col" className="pb-2 pr-3 font-medium">Response time</th>
                  <th scope="col" className="pb-2 pr-3 font-medium">Output tokens</th>
                  <th scope="col" className="pb-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-b border-black/5 last:border-0">
                    <th scope="row" className="max-w-[160px] py-2 pr-3 font-medium text-ink">
                      <span className="block truncate" title={r.modelId}>{r.name}</span>
                      {r.fallback && (
                        <span className="block truncate text-[10px] font-normal text-amber-600">via {r.fallback}</span>
                      )}
                    </th>
                    <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-neutral-500">{formatDuration(r.timeMs)}</td>
                    <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-neutral-500">{formatTokens(r.outputTokens)}</td>
                    <td className="whitespace-nowrap py-2">
                      <span className={`label-xs rounded-full px-2 py-0.5 ${STATUS_CLASS[r.status]}`}>{r.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
