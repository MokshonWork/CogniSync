import { modelStatus } from './modelStatus.js'
import { rankingOutcome, rankBarWidth } from './rankings.js'

const STATUS_CLASS = {
  Completed: 'bg-gpt/10 text-gpt',
  Failed: 'bg-red-500/10 text-red-600',
  'Timed out': 'bg-amber-500/10 text-amber-700',
  Incomplete: 'bg-amber-500/10 text-amber-700',
  Running: 'bg-amber-500/10 text-amber-600',
}

function formatRank(value) {
  return Number.isFinite(value) ? String(Number(value.toFixed(2))) : '—'
}

function responseStatus(r) {
  return modelStatus(r)
}

export default function ModelComparison({
  responses = [],
  aggregateRankings = [],
  peerReviews = [],
  criteria = [],
  loading = false,
  models = [],
}) {
  const outcome = rankingOutcome(aggregateRankings, peerReviews)
  const responseRows = responses.map((r) => ({
    key: r.model_id || r.model,
    modelId: r.model_id || r.model,
    name: r.model || r.model_id || 'Unknown model',
    status: responseStatus(r),
    error: r.error || null,
    text: r.text || '',
    color: models.find((m) => m.key === r.model_id)?.color || '#9CA3AF',
  }))
  const byId = new Map(responseRows.map((r) => [r.modelId, r]))
  const rankedIds = new Set(outcome.rows.map((r) => r.model_id))
  const unranked = responseRows.filter((r) => !rankedIds.has(r.modelId))
  const labels = (Array.isArray(criteria) ? criteria : [])
    .map((c) => (typeof c === 'string' ? c : c?.label || c?.name || c?.id))
    .filter(Boolean)

  return (
    <section
      aria-labelledby="model-comparison-title"
      className="mt-4 rounded-2xl border border-black/10 bg-white p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="model-comparison-title" className="text-[14px] font-semibold text-ink">Model Comparison</h2>
          <p className="mt-0.5 text-[11px] text-neutral-400">
            Real peer-review rankings from this run. Lower average rank is better.
          </p>
        </div>
        <span className="label-xs rounded-full border border-black/10 px-2 py-1 text-neutral-400">This run</span>
      </div>

      {responseRows.length === 0 && (
        <p className="mt-4 rounded-xl bg-black/[0.03] px-3 py-4 text-[12px] text-neutral-400" role="status">
          {loading ? 'Waiting for model responses and peer rankings…' : 'No model responses were recorded for this run.'}
        </p>
      )}

      {responseRows.length > 0 && outcome.rows.length === 0 && (
        <div className="mt-4">
          <p className="rounded-xl bg-black/[0.03] px-3 py-3 text-[12px] text-neutral-500" role="status">
            {loading
              ? 'Peer rankings are still being collected — no winner is declared yet. Response status is shown below.'
              : 'Peer ranking scores are unavailable for this run, so no winner is declared. Response status is shown below.'}
          </p>
          <StatusList rows={unranked} />
        </div>
      )}

      {outcome.rows.length > 0 && (
        <>
          <p className="mt-3 rounded-xl bg-sage/70 px-3 py-2 text-[12px] leading-relaxed text-neutral-600">
            {outcome.winner ? (
              <>
                <span className="font-semibold text-ink">{outcome.winner.model}</span> is top-ranked by peer review — average rank{' '}
                {formatRank(outcome.winner.average_rank)} across {outcome.winner.rankings_count} ranking
                {outcome.winner.rankings_count === 1 ? '' : 's'}
                {outcome.firstPlaceVotes > 0 && (
                  <> and {outcome.firstPlaceVotes} first-place vote{outcome.firstPlaceVotes === 1 ? '' : 's'}</>
                )}
                . This reflects answer-to-answer comparisons, not an accuracy guarantee.
              </>
            ) : (
              <>
                Tied for top rank: <span className="font-semibold text-ink">{outcome.leaders.map((r) => r.model).join(', ')}</span>{' '}
                at average rank {formatRank(outcome.leaders[0].average_rank)}. No single winner is declared.
              </>
            )}
          </p>

          <ul className="mt-4 flex flex-col gap-3" aria-label="Models ordered by average peer rank, best first">
            {outcome.rows.map((row) => {
              const response = byId.get(row.model_id)
              return (
                <li key={row.model_id}>
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12px]">
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: response?.color || '#9CA3AF' }}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate text-neutral-700" title={row.model_id}>{row.model}</span>
                    <span className="shrink-0 tabular-nums text-neutral-500">
                      avg rank {formatRank(row.average_rank)}
                      <span className="text-neutral-400"> · {row.rankings_count} review{row.rankings_count === 1 ? '' : 's'}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-black/5" aria-hidden="true">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${rankBarWidth(row.average_rank, outcome.rows)}%`,
                        background: response?.color || '#9CA3AF',
                        opacity: row.model_id === outcome.winner?.model_id ? 1 : 0.65,
                      }}
                    />
                  </div>
                  {row.model_id === outcome.winner?.model_id && (
                    <p className="mt-1 text-[10px] text-gpt">Top-ranked by peer review</p>
                  )}
                </li>
              )
            })}
          </ul>

          <p className="mt-3 text-[11px] leading-relaxed text-neutral-400">
            Criteria: {labels.length
              ? labels.join(', ')
              : 'open-ended peer review — each reviewer explains strengths and weaknesses, then ranks every response. The backend does not report per-criterion scores.'}
          </p>

          {unranked.length > 0 && <StatusList rows={unranked} title="Not ranked" />}
        </>
      )}
    </section>
  )
}

function StatusList({ rows, title = 'Response status' }) {
  return (
    <div className="mt-4 border-t border-black/5 pt-3">
      <h3 className="label-xs text-neutral-400">{title}</h3>
      <ul className="mt-2 flex flex-col gap-2">
        {rows.map((r) => (
          <li key={r.key} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: r.color }} aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate text-neutral-600" title={r.modelId}>{r.name}</span>
            <span className={`label-xs shrink-0 rounded-full px-2 py-0.5 ${STATUS_CLASS[r.status] || 'bg-black/5 text-neutral-500'}`}>
              {r.status}
            </span>
            {r.error && r.status !== 'Running' && (
              <span className="w-full truncate text-[11px] text-neutral-400" title={r.error}>{r.error}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
