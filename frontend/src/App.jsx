import { useCallback, useEffect, useRef, useState } from 'react'
import Sidebar from './Sidebar.jsx'
import MainPanel from './MainPanel.jsx'
import RightPanel from './RightPanel.jsx'
import HistoryView from './views/HistoryView.jsx'
import LibraryView from './views/LibraryView.jsx'
import ModelsView from './views/ModelsView.jsx'
import CompareView from './views/CompareView.jsx'
import ToolsView from './views/ToolsView.jsx'
import SettingsView from './views/SettingsView.jsx'
import { api, runCouncilStream } from './api.js'
import { mapRunToResults } from './runResults.js'
import { loadPrefs, savePrefs } from './prefs.js'

// Peers reading persisted data (Sidebar recents, History) refresh when the
// server-side conversation state changes.
const CONVERSATIONS_CHANGED = 'cognisync:conversations-changed'
const emitConversationsChanged = () => window.dispatchEvent(new Event(CONVERSATIONS_CHANGED))

const STEPS = ['Ask a question', 'Models respond', 'Council synthesizes']

// Fresh progressive results for a run: one card per model before any
// response arrives; the authoritative id list arrives in stage1_start.
function initialRunResults(question) {
  return {
    question,
    model_responses: [],
    synthesis: '',
    final_answer: null,
    final_answer_error: null,
    synthesis_error: null,
    discussion: null, // null = still processing → timeline shows real progress
    search: { enabled: false },
    sources: null,
    failed: false,
    streaming: true,
    reviewProgress: null,
    note: null,
  }
}

// Convert a stage1_result event into a card entry (display name falls back
// to the raw id until the final payload carries catalog names).
function cardFromEvent(ev, names) {
  return {
    model_id: ev.model_id || ev.model,
    model: (names && (names[ev.model] || names[ev.model_id || ev.model])) || ev.model_id || ev.model,
    text: ev.success ? (ev.response || '') : '',
    success: ev.success,
    state: ev.success ? 'completed' : 'failed',
    tokens: ev.tokens || 0,
    time_ms: ev.duration_ms || 0,
    error: ev.error || null,
  }
}

function applyEvent(prev, event, questionText, conversationId, myRunId, namesRef) {
  switch (event.type) {
    case 'stage1_start': {
      const ids = event.models || []
      namesRef.current = event.names || {}
      const prior = new Map((prev.model_responses || []).map((m) => [m.model_id, m]))
      return {
        ...prev,
        model_responses: ids.map((id) => prior.get(id) || {
          model_id: id,
          model: (event.names && (event.names[id] || event.names[id])) || id,
          text: '', success: null, state: 'running',
          tokens: 0, time_ms: 0, error: null,
        }),
      }
    }
    case 'stage1_result': {
      const name = (namesRef.current && namesRef.current[event.model]) || event.model_id || event.model
      const card = cardFromEvent(event, { [event.model]: name })
      const existing = (prev.model_responses || []).some((m) => m.model_id === card.model_id)
      return {
        ...prev,
        model_responses: existing
          ? prev.model_responses.map((m) => (m.model_id === card.model_id ? { ...m, ...card } : m))
          : [...(prev.model_responses || []), card],
      }
    }
    case 'stage2_start':
      return { ...prev, discussion: null, reviewProgress: { done: 0, total: (event.reviewers || []).length } }
    case 'stage2_result':
      return {
        ...prev,
        reviewProgress: {
          done: (prev.reviewProgress?.done || 0) + 1,
          total: prev.reviewProgress?.total || 0,
          lastError: event.success ? prev.reviewProgress?.lastError : (event.error || 'review failed'),
        },
      }
    case 'rankings':
      return { ...prev, reviewProgress: { ...prev.reviewProgress, finished: true } }
    case 'stage3_start':
      return prev
    case 'stage3_result':
      return {
        ...prev,
        synthesis: event.stage3.success ? event.stage3.response : '',
        final_answer: event.stage3.success ? (event.stage3.response || null) : null,
        final_answer_error: event.stage3.success ? null : (event.stage3.error || 'Chairman synthesis failed.'),
        synthesis_error: event.stage3.success ? null : event.stage3.error,
      }
    case 'final': {
      const built = mapRunToResults(event.payload, questionText)
      return built
    }
    case 'note':
      return { ...prev, note: event.message }
    case 'error':
      return { ...prev, failed: true, streaming: false, discussion: [{ type: 'Synthesis', text: event.message }] }
    default:
      return prev
  }
}

export default function App() {
  const [view, setView] = useState('chat')
  const [collapsed, setCollapsed] = useState(() => loadPrefs().collapseSidebar)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [focusSignal, setFocusSignal] = useState(0)
  const [prefs, setPrefs] = useState(loadPrefs)
  const [lastPayload, setLastPayload] = useState(null)
  const [conversationId, setConversationId] = useState(null)
  const runIdRef = useRef(0)
  const abortRef = useRef(null)
  const namesRef = useRef({})
  // One stable conversation id per session. A new session generates a UUID
  // locally; the backend creates the record only after the request validates,
  // so rejected/empty requests never leave "New Conversation" debris.
  const conversationIdRef = useRef(null)

  useEffect(() => { savePrefs(prefs) }, [prefs])

  const send = useCallback(async ({ question, models, webSearch }) => {
    const questionText = (question || '').trim()
    if (!questionText) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const myRunId = ++runIdRef.current
    let convId = conversationIdRef.current
    if (!convId) {
      convId = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
      conversationIdRef.current = convId
    }
    setConversationId(convId)
    setLoading(true)
    setLastPayload({ question: questionText, models, webSearch })
    setView('chat')
    setResults(initialRunResults(questionText))

    try {
      await runCouncilStream(
        convId,
        { content: questionText, models: models || undefined, webSearch },
        (event) => {
          if (runIdRef.current !== myRunId) return // newer run owns the view
          setResults((prev) => {
            const hasStreamingState = prev && prev.question === questionText
            if (event.type === 'final') return mapRunToResults(event.payload, questionText)
            if (!hasStreamingState) return prev // stale/foreign event
            return applyEvent(prev, event, questionText, convId, myRunId, namesRef)
          })
        },
        controller.signal,
      )
      if (controller.signal.aborted || runIdRef.current !== myRunId) return
      // Stream ended without a final event (backend crash mid-run):
      // settle honestly instead of staying forever loading.
      setResults((prev) => (prev && prev.streaming
        ? (prev.final_answer
            ? { ...prev, streaming: false }
            : {
                ...prev,
                streaming: false,
                failed: true,
                discussion: [{ type: 'Synthesis', text: 'Connection ended before the council finished. Model answers above are preserved — retry to continue.' }],
              })
        : prev))
    } catch (e) {
      if (controller.signal.aborted || runIdRef.current !== myRunId) return
      setResults((prev) => ({
        ...(prev || {}),
        question: questionText,
        model_responses: prev?.model_responses || [],
        synthesis: prev?.synthesis || '',
        final_answer: prev?.final_answer || null,
        final_answer_error: prev?.final_answer_error || null,
        synthesis_error: prev?.synthesis_error || null,
        discussion: [{ type: 'Synthesis', text: `Request failed: ${e.message}` }],
        search: { enabled: !!webSearch, error: e.message },
        failed: true,
        streaming: false,
        reviewProgress: null,
      }))
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      if (runIdRef.current === myRunId) {
        setLoading(false)
        emitConversationsChanged()
      }
    }
  }, [])

  const retryLast = useCallback(() => {
    if (lastPayload) send(lastPayload)
  }, [lastPayload, send])

  const stopRun = useCallback(() => {
    runIdRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
    setResults((prev) => (prev && prev.streaming
      ? {
          ...prev,
          streaming: false,
          discussion: [{ type: 'Synthesis', text: 'Run cancelled — model answers above are preserved.' }],
        }
      : prev))
  }, [])

  const newSession = useCallback(() => {
    runIdRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setResults(null)
    setLoading(false)
    conversationIdRef.current = null
    setConversationId(null)
    setLastPayload(null)
    setView('chat')
    setFocusSignal((v) => v + 1)
  }, [])

  // Open a saved conversation in the workspace: hydrate the last exchange
  // from persistence so follow-ups continue in the SAME conversation id.
  const continueConversation = useCallback(async (id) => {
    runIdRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    conversationIdRef.current = id
    setConversationId(id)
    setView('chat')
    try {
      const conv = await api.getConversation(id)
      const msgs = conv.messages || []
      const lastAssistant = [...msgs].reverse().find((m) => m.role === 'assistant')
      const lastUser = [...msgs].reverse().find((m) => m.role === 'user')
      const question = (() => {
        const c = lastUser?.content || ''
        try { const p = JSON.parse(c); return p.content || c } catch { return c }
      })()
      setResults(lastAssistant
        ? { ...mapRunToResults(lastAssistant, question), conversationId: id }
        : null)
      setLoading(false)
    } catch {
      setResults(null)
    }
    setFocusSignal((v) => v + 1)
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-canvas font-sans text-ink">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => {
          const next = !collapsed
          setCollapsed(next)
          setPrefs((p) => ({ ...p, collapseSidebar: next }))
        }}
        onNewSession={newSession}
        onViewChange={setView}
        view={view}
        onOpenConversation={continueConversation}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {view === 'chat' ? (
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <MainPanel
              conversationId={conversationId}
              results={results}
              loading={loading}
              onSend={send}
              onRetry={retryLast}
              onCancel={stopRun}
              focusSignal={focusSignal}
              defaultMode={prefs.mode}
              selected={prefs.selectedModels}
              onToggleSelected={(key) => setPrefs((p) => ({
                ...p,
                selectedModels: p.selectedModels.includes(key)
                  ? p.selectedModels.filter((k) => k !== key)
                  : [...p.selectedModels, key],
              }))}
              webSearch={prefs.webSearch}
              onOpenTools={() => setView('tools')}
            />
            <RightPanel
              results={results}
              webSearch={prefs.webSearch}
              onToggleWebSearch={() => setPrefs((p) => ({ ...p, webSearch: !p.webSearch }))}
              selectedModels={prefs.selectedModels}
              onOpenTools={() => setView('tools')}
              onRetry={retryLast}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {view === 'history' && (
              <HistoryView onContinue={continueConversation} />
            )}
            {view === 'library' && (
              <LibraryView
                onUsePrompt={(text) => {
                  setView('chat')
                  setFocusSignal((v) => v + 1)
                  window.dispatchEvent(new CustomEvent('cognisync:use-prompt', { detail: text }))
                }}
              />
            )}
            {view === 'models' && (
              <ModelsView
                selectedModels={prefs.selectedModels}
                onChangeSelected={(selectedModels) => setPrefs((p) => ({ ...p, selectedModels }))}
              />
            )}
            {view === 'compare' && <CompareView results={results} onNewRequest={newSession} />}
            {view === 'tools' && (
              <ToolsView
                webSearch={prefs.webSearch}
                onToggleWebSearch={() => setPrefs((p) => ({ ...p, webSearch: !p.webSearch }))}
              />
            )}
            {view === 'settings' && <SettingsView prefs={prefs} onPrefsChange={setPrefs} />}
          </div>
        )}

        {view === 'chat' && (
          <footer className="flex shrink-0 items-center justify-center gap-10 border-t border-black/5 bg-canvas px-8 py-3">
            {STEPS.map((s, i) => (
              <div key={s} className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">
                    {i + 1}
                  </span>
                  <span className="text-[12px] text-neutral-500">{s}</span>
                </div>
                {i < STEPS.length - 1 && <span className="text-neutral-300">→</span>}
              </div>
            ))}
          </footer>
        )}
      </div>
    </div>
  )
}
