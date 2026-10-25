// OpenRouter free-model catalog, fetched from the backend once and cached
// for the session. The backend does the real catalog work and verifies
// pricing; this module is a thin cache so views don't refetch on every
// render, and exposeModelCatalog(force) gives an explicit refresh path.
import { useEffect, useState } from 'react'

const TTL_MS = 10 * 60 * 1000

let cache = { models: null, fetchedAt: 0, meta: null, inflight: null }
const FALLBACK_COLORS = ['#22C55E', '#FF6B4A', '#4A90D9', '#A78BFA', '#F59E0B', '#10B981']

function colorFor(key) {
  let hash = 0
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  return FALLBACK_COLORS[hash % FALLBACK_COLORS.length]
}

async function fetchCatalog(force = false) {
  const base = import.meta.env.VITE_API_URL || 'http://localhost:8001'
  const path = force ? '/api/models/refresh' : '/api/models'
  const res = await fetch(`${base}${path}`, { method: force ? 'POST' : 'GET' })
  if (!res.ok) throw new Error(`Failed to load model catalog (${res.status})`)
  const data = await res.json()
  const models = (data.models || []).map((m) => ({
    key: m.id,
    id: m.id,
    name: m.name || m.id,
    family: m.family || 'other',
    color: colorFor(m.family || m.id),
    contextLength: m.context_length,
    fallback: !!m.fallback,
  }))
  cache = { models, fetchedAt: Date.now(), meta: data, inflight: null }
  return cache
}

export function exposeModelCatalog(force = false) {
  const fresh = !force && cache.models && Date.now() - cache.fetchedAt < TTL_MS
  if (fresh || cache.inflight) return cache.inflight || Promise.resolve(cache)
  cache.inflight = fetchCatalog(force)
    .catch((e) => {
      cache.inflight = null
      throw e
    })
  return cache.inflight
}

export function cachedModels() {
  return cache.models
}

export function catalogMeta() {
  return cache.meta
}

export function useModelCatalog() {
  // Always expose a plain array — consumers map/filter it directly and must
  // not crash during the first-paint window before the fetch resolves.
  const [state, setState] = useState({ models: cache.models || [], meta: cache.meta, loading: !cache.models, error: null })

  useEffect(() => {
    let cancelled = false
    exposeModelCatalog()
      .then((c) => { if (!cancelled) setState({ models: c.models || [], meta: c.meta, loading: false, error: null }) })
      .catch((e) => { if (!cancelled) setState({ models: cache.models || [], meta: null, loading: false, error: e.message }) })
    return () => { cancelled = true }
  }, [])

  return state
}
