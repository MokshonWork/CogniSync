import { useState, useEffect } from 'react'
import { Icon } from '../icons.jsx'
import { getToolsStatus, testTool, githubSearch, notionSearch } from '../api.js'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'

function Badge({ label, tone }) {
  const tones = {
    ok: 'bg-gpt/10 text-gpt',
    warn: 'bg-amber-100 text-amber-700',
    bad: 'bg-red-500/10 text-red-600',
  }
  return <span className={`label-xs rounded-full px-2 py-0.5 ${tones[tone] || 'bg-black/5 text-neutral-500'}`}>{label}</span>
}

// State derivation shared by Tools page and sidebar (same backend source):
// patched together from configuration plus the latest live test outcome.
function stateFor(integration, lastTest, testing) {
  if (testing) return { badge: 'Testing…', tone: 'warn', connected: false }
  if (lastTest?.result?.success) return { badge: 'Connected', tone: 'ok', connected: true }
  if (lastTest?.result && !lastTest.result.success) return { badge: 'Error', tone: 'bad', connected: false, error: lastTest.result.error }
  if (!integration?.configured) return { badge: 'Unavailable', tone: undefined, connected: false }
  return { badge: 'Available (unverified)', tone: 'warn', connected: false }
}

function useToolsStatus() {
  const [status, setStatus] = useState(null)
  const [lastTests, setLastTests] = useState({})
  const [testing, setTesting] = useState({})
  const [loadError, setLoadError] = useState(null)

  const load = async () => {
    try {
      const data = await getToolsStatus()
      setLoadError(null)
      setStatus(data)
    } catch {
      setLoadError('Could not load integration status from the backend.')
    }
  }

  useEffect(() => {
    load()
    const iv = window.setInterval(load, 30000)
    return () => { window.clearInterval(iv) }
  }, [])

  const runTest = async (name) => {
    setTesting((t) => ({ ...t, [name]: true }))
    try {
      const result = await testTool(name)
      setLastTests((lt) => ({ ...lt, [name]: { result, at: new Date() } }))
    } catch (e) {
      setLastTests((lt) => ({ ...lt, [name]: { result: { success: false, error: e.message }, at: new Date() } }))
    } finally {
      setTesting((t) => ({ ...t, [name]: false }))
    }
  }

  return { status, lastTests, testing, runTest, loadError, reload: load }
}

function PanelRun({ result }) {
  if (!result) return null
  if (!result.success) {
    return <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-[11px] text-red-600">{result.error}</p>
  }
  if (result.data?.detail) return <p className="mt-2 rounded-lg bg-gpt/10 px-3 py-2 text-[11px] text-gpt">{result.data.detail}</p>
  if (result.data?.detail === undefined && result.duration_ms != null) {
    return <p className="mt-2 rounded-lg bg-gpt/10 px-3 py-2 text-[11px] text-gpt">Works ({result.duration_ms} ms).</p>
  }
  return null
}

function GithubCard({ integration, state, lastTest, testing, onTest }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const run = async (fn, ...args) => {
    setBusy(true)
    setErr(null)
    try {
      const r = await fn(...args)
      if (!r.success) { setErr(r.error); setResults(null) }
      else setResults(r.data)
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="rounded-2xl border border-black/10 bg-white p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] font-semibold text-ink">GitHub</div>
          <p className="mt-0.5 text-[12px] text-neutral-400">{integration.note}</p>
        </div>
        <Badge label={state.badge} tone={state.tone} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2 border-t border-black/5 pt-3">
        <button onClick={() => onTest('github')} disabled={testing.github} className="rounded-lg bg-ink px-3 py-1.5 text-[12px] text-white disabled:opacity-40">
          {testing.github ? 'Testing…' : 'Test connection'}
        </button>
        {!integration.configured ? (
          <span className="text-[11px] text-amber-700">Setup: add <code>GITHUB_TOKEN</code> to the backend environment and restart.</span>
        ) : null}
      </div>
      <PanelRun result={lastTest?.result} />
      {state.connected && (
        <div className="mt-3 border-t border-black/5 pt-3">
          <p className="label-xs text-neutral-400">Read-only tools</p>
          <div className="mt-2 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run(githubSearch, query)}
              placeholder="Search repositories…"
              className="flex-1 rounded-lg border border-black/10 px-3 py-1.5 text-[12px]"
            />
            <button onClick={() => run(githubSearch, query)} disabled={busy || !query.trim()} className="rounded-lg bg-black/5 px-3 py-1.5 text-[12px] text-neutral-700 hover:bg-black/10 disabled:opacity-30">Search</button>
          </div>
          {results?.results && (
            <ul className="mt-2 flex flex-col gap-1.5">
              {results.results.map((r) => (
                <li key={r.full_name} className="rounded-lg bg-black/[0.03] px-3 py-1.5 text-[12px]">
                  <a href={r.url} target="_blank" rel="noreferrer" className="font-medium text-ink hover:underline">{r.full_name}</a>
                  <span className="ml-2 text-neutral-400">★ {r.stars} {r.language || ''}</span>
                  <p className="text-[11px] text-neutral-500">{r.description}</p>
                </li>
              ))}
              {!results.results.length && <li className="text-[11px] text-neutral-400">No repositories matched.</li>}
            </ul>
          )}
          {err && <p className="mt-2 text-[11px] text-red-600">{err}</p>}
        </div>
      )}
    </div>
  )
}

function NotionCard({ integration, state, lastTest, testing, onTest }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const run = async (fn, ...args) => {
    setBusy(true)
    setErr(null)
    try {
      const r = await fn(...args)
      if (!r.success) { setErr(r.error); setResults(null) }
      else setResults(r.data)
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="rounded-2xl border border-black/10 bg-white p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] font-semibold text-ink">Notion</div>
          <p className="mt-0.5 text-[12px] text-neutral-400">{integration.note}</p>
        </div>
        <Badge label={state.badge} tone={state.tone} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2 border-t border-black/5 pt-3">
        <button onClick={() => onTest('notion')} disabled={testing.notion} className="rounded-lg bg-ink px-3 py-1.5 text-[12px] text-white disabled:opacity-40">
          {testing.notion ? 'Testing…' : 'Test connection'}
        </button>
        {!integration.configured ? (
          <span className="text-[11px] text-amber-700">Setup: set <code>NOTION_TOKEN</code> server-side and share pages with the integration in Notion.</span>
        ) : null}
      </div>
      <PanelRun result={lastTest?.result} />
      {state.connected && (
        <div className="mt-3 border-t border-black/5 pt-3">
          <p className="label-xs text-neutral-400">Search shared pages</p>
          <div className="mt-2 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run(notionSearch, query)}
              placeholder="Find pages…"
              className="flex-1 rounded-lg border border-black/10 px-3 py-1.5 text-[12px]"
            />
            <button onClick={() => run(notionSearch, query)} disabled={busy} className="rounded-lg bg-black/5 px-3 py-1.5 text-[12px] text-neutral-700 hover:bg-black/10 disabled:opacity-30">Search</button>
          </div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {(results?.results || []).map((r) => (
              <li key={r.id} className="rounded-lg bg-black/[0.03] px-3 py-1.5 text-[12px]">
                <a href={r.url} target="_blank" rel="noreferrer" className="font-medium text-ink hover:underline">{r.title}</a>
                <span className="ml-2 text-neutral-400">{r.type}</span>
              </li>
            ))}
            {results && !results.results.length && <li className="text-[11px] text-neutral-400">Nothing shared with the integration matched.</li>}
          </ul>
          {err && <p className="mt-2 text-[11px] text-red-600">{err}</p>}
        </div>
      )}
    </div>
  )
}

export default function ToolsView({ webSearch, onToggleWebSearch }) {
  const { status, lastTests, testing, runTest, loadError } = useToolsStatus()

  const integrations = status?.integrations || {}
  const webSearchIntegration = integrations.web_search || {}
  const github = integrations.github || {}
  const notion = integrations.notion || {}

  return (
    <section className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="font-display text-[28px] font-semibold text-ink">Tools</h1>
      <p className="text-[13px] text-neutral-400">Connection types are labeled honestly: official REST APIs, a library-based DuckDuckGo search, and no MCP transport anywhere. Badges reflect real configured state; “Connected” appears only after a live test succeeds.</p>

      {loadError && (
        <div className="mt-4 rounded-xl border border-black/10 bg-white p-4">
          <p className="text-[13px] text-red-500">{loadError}</p>
          <button onClick={() => window.location.reload()} className="mt-2 rounded-lg bg-ink px-3 py-1.5 text-[12px] text-white">Retry</button>
        </div>
      )}

      {status && (
        <div className="mt-6 flex flex-col gap-4">
          {/* Authentication state */}
          <div className="rounded-2xl border border-black/10 bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[14px] font-semibold text-ink">Authentication</div>
                <p className="mt-0.5 text-[12px] text-neutral-400">
                  {status.authentication === 'none'
                    ? 'No user accounts in this deployment. Integrations use server-side credentials (environment variables) — nothing per-user and nothing in the browser.'
                    : status.authentication}
                </p>
              </div>
              <Badge label={status.authentication === 'none' ? 'Server-side credentials' : status.authentication} />
            </div>
          </div>

          {/* DuckDuckGo Search — library-based, no API key */}
          <div className="rounded-2xl border border-black/10 bg-white p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[14px] font-semibold text-ink">DuckDuckGo Search</div>
                <p className="mt-0.5 text-[12px] text-neutral-400">{webSearchIntegration.note}</p>
              </div>
              <Badge label={stateFor(webSearchIntegration, lastTests.web_search, testing.web_search).badge} tone={stateFor(webSearchIntegration, lastTests.web_search, testing.web_search).tone} />
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-black/5 pt-3">
              <span className="text-[13px] text-neutral-600">Use DuckDuckGo search for council requests</span>
              <button
                onClick={onToggleWebSearch}
                disabled={!webSearchIntegration.configured}
                title="No API key required"
                className={`relative h-5 w-9 rounded-full transition-colors ${webSearch && webSearchIntegration.configured ? 'bg-gpt' : 'bg-black/10 disabled:opacity-40'}`}
                aria-label="Toggle DuckDuckGo web search for council requests"
              >
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${webSearch && webSearchIntegration.configured ? 'left-[18px]' : 'left-0.5'}`} />
              </button>
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button onClick={() => runTest('web_search')} disabled={testing.web_search} className="rounded-lg bg-ink px-3 py-1.5 text-[12px] text-white disabled:opacity-40">
                {testing.web_search ? 'Testing…' : 'Test search'}
              </button>
              <span className="text-[11px] text-neutral-400">Library-based DuckDuckGo search (ddgs). No API key or account required.</span>
            </div>
            <PanelRun result={lastTests.web_search?.result} />
          </div>

          <GithubCard
            integration={github}
            state={stateFor(github, lastTests.github, testing.github)}
            lastTest={lastTests.github}
            testing={testing}
            onTest={runTest}
          />
          <NotionCard
            integration={notion}
            state={stateFor(notion, lastTests.notion, testing.notion)}
            lastTest={lastTests.notion}
            testing={testing}
            onTest={runTest}
          />

          <p className="text-[11px] text-neutral-400">
            DuckDuckGo search is a library integration, not an MCP server. GitHub and Notion use their official REST APIs. Status source: <a href={`${API_BASE}/api/tools/status`} target="_blank" rel="noreferrer" className="underline">{API_BASE}/api/tools/status</a>
          </p>
        </div>
      )}
    </section>
  )
}
