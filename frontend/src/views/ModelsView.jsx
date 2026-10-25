import { useState, useEffect } from 'react'
import { familyIcon } from '../icons.jsx'
import { exposeModelCatalog } from '../catalog.js'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'

// Catalog-driven council settings. The backend verifies free-eligibility;
// this view reflects that catalog and the OpenRouter key configuration.
// It deliberately does NOT claim models are live/online — only the catalog
// state, which is distinct from successful connectivity.
export default function ModelsView({ selectedModels, onChangeSelected }) {
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(null)
  const [models, setModels] = useState([])
  const [catalogMeta, setCatalogMeta] = useState(null)
  const [catalogError, setCatalogError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/`)
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => setConfigError('Could not reach the backend configuration.'))
  }, [])

  const loadCatalog = async (force = false) => {
    setRefreshing(true)
    try {
      const c = await exposeModelCatalog(force)
      setModels(c.models)
      setCatalogMeta(c.meta)
      setCatalogError(c.models?.length ? null : 'The catalog lists no free-eligible models right now.')
    } catch (e) {
      setCatalogError(e.message)
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => { loadCatalog(false) }, [])

  const selected = (id) => selectedModels.includes(id)

  return (
    <section className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="font-display text-[28px] font-semibold text-ink">Model settings</h1>
      <p className="text-[13px] text-neutral-400">Live catalog from OpenRouter via the backend, plus your council participation defaults.</p>

      {configError && <p className="mt-4 text-[13px] text-red-500">{configError}</p>}
      {catalogError && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-700">
          {catalogError}{' '}
          <button onClick={() => loadCatalog(true)} disabled={refreshing} className="ml-1 underline">
            {refreshing ? 'Refreshing…' : 'Retry'}
          </button>
        </div>
      )}

      <div className="mt-6 flex flex-col gap-4">
        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[14px] font-semibold text-ink">Council members</h2>
            <div className="flex items-center gap-2 text-[11px] text-neutral-400">
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: config?.providers?.openrouter?.configured ? '#22C55E' : '#f87171' }} />
              OpenRouter key {config?.providers?.openrouter?.configured ? 'configured' : 'missing'}
            </div>
          </div>
          <p className="mt-1 text-[11px] text-neutral-400">
            Free-testing mode only uses models verified free in the OpenRouter catalog (zero pricing, `:free` variants).
            {catalogMeta?.fallback && ' Catalog is currently unreachable — the documented fallback list is shown and NOT verified against pricing.'}
          </p>
          <div className="mt-3 flex flex-col gap-2">
            {models.map((m) => {
              const MIcon = familyIcon(m.family)
              const checked = selected(m.key)
              return (
                <label key={m.key} className="flex cursor-pointer items-center gap-3 rounded-lg border border-black/5 px-3 py-2.5">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onChangeSelected(checked ? selectedModels.filter((k) => k !== m.key) : [...selectedModels, m.key])}
                    className="h-3.5 w-3.5 accent-black"
                  />
                  <span className="flex h-6 w-6 items-center justify-center rounded-md" style={{ background: `${m.color}18`, color: m.color }}>
                    <MIcon size={13} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-neutral-700">
                    {m.name} <span className="text-neutral-400">· {m.key}</span>
                    {catalogMeta?.free_testing && <span className="ml-2 rounded-full bg-sage px-2 py-0.5 text-[10px] text-ink">free</span>}
                  </span>
                  {/* catalog state only — live connectivity is verified per-request */}
                  <span className="label-xs text-neutral-400">{m.contextLength ? `${Math.round(m.contextLength / 1000)}k ctx` : 'catalog'}</span>
                </label>
              )
            })}
            {!models.length && !catalogError && <p className="text-[12px] text-neutral-400">Loading catalog…</p>}
          </div>
          <p className="mt-2 text-[11px] text-neutral-400">
            An empty selection means “use every free-eligible model the catalog lists”. Being catalog-listed is free-eligibility, not a guarantee of quota or availability — individual requests can still fail.
          </p>
        </div>

        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <h2 className="text-[14px] font-semibold text-ink">Chairman (synthesis)</h2>
          <p className="mt-1 text-[12px] text-neutral-500">
            Set on the server via <code className="rounded bg-black/5 px-1">CHAIRMAN_MODEL</code>: <span className="font-medium text-ink">{config?.chairman_model || 'unknown'}</span>. Changing it requires editing server config — not exposed for safety.
          </p>
        </div>
      </div>
    </section>
  )
}
