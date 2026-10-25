const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'

// Keeps the legacy `api` object for the older components/* tree.
export const api = {
  async listConversations() {
    const res = await fetch(`${API_BASE}/api/conversations`)
    if (!res.ok) throw new Error('Failed to list conversations')
    return res.json()
  },
  async createConversation() {
    const res = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    if (!res.ok) throw new Error('Failed to create conversation')
    return res.json()
  },
  async getConversation(conversationId) {
    const res = await fetch(`${API_BASE}/api/conversations/${conversationId}`)
    if (!res.ok) throw new Error('Failed to get conversation')
    return res.json()
  },
  async sendMessage(conversationId, content) {
    const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
    if (!res.ok) throw new Error('Failed to send message')
    return res.json()
  },
  async sendMessageStream(conversationId, content, onEvent) {
    const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/message/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
    if (!res.ok) throw new Error('Failed to send message')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() // keep incomplete last line for the next chunk
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6))
            onEvent(event.type, event)
          } catch {
            // ignore malformed keep-alive lines
          }
        }
      }
    }
  },
}

export async function queryCouncil({ question, models = [], mode = 'balanced', webSearch = false }) {
  const res = await fetch(`${API_BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, models, mode, web_search: webSearch }),
  })
  if (!res.ok) {
    let detail = `Query failed: ${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* non-JSON error */ }
    throw new Error(detail)
  }
  return res.json()
}

// Conversation-based council run. The backend persists the user message,
// runs all three stages, and returns the full result. Network errors are
// never silently swallowed — the caller shows the retry path.
export async function sendCouncilMessage(conversationId, { content, webSearch = false }) {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, web_search: webSearch }),
  })
  if (!res.ok) {
    let detail = `Message failed: ${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* non-JSON error body */ }
    if (res.status === 503) detail = 'Backend unreachable — is the server running on port 8001?'
    throw new Error(detail)
  }
  return res.json()
}

// Progressive council run over Server-Sent Events. Calls onEvent once per
// backend event (user_message, stage1_result as each model finishes,
// stage2_result per reviewer, rankings, stage3_result, final, done).
// Throws synchronously on connection failure; individual model failures
// arrive as events instead. HTTP errors surface as a thrown Error so the
// caller can distinguish "backend down" from "some models failed".
export async function runCouncilStream(conversationId, { content, models, webSearch = false }, onEvent, signal) {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/message/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ content, web_search: webSearch, models }),
    signal,
  })
  if (!res.ok) {
    let detail = `Council request failed: ${res.status}`
    try { const body = await res.json(); detail = body.detail || detail } catch { /* non-JSON body */ }
    if (res.status === 503) detail = 'Backend unreachable — is the server running on port 8001?'
    throw new Error(detail)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    let done = false
    let value = null
    try {
      const chunk = await reader.read()
      done = chunk.done
      value = chunk.value
    } catch (e) {
      if (e.name === 'AbortError') return
      throw new Error(`Connection lost during council run: ${e.message}`)
    }
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      let event
      try {
        event = JSON.parse(line.slice(6))
      } catch {
        continue // ignore malformed/keep-alive lines
      }
      onEvent(event)
      if (event.type === 'done') return
    }
  }
}

// Explicit live connection test for one integration (web_search | github | notion)
export async function testTool(name) {
  const res = await fetch(`${API_BASE}/api/tools/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok && !data.error) throw new Error(`Test failed: ${res.status}`)
  return { name, ...data }
}

// Read-only GitHub / Notion tool invocations
export async function githubSearch(query) {
  const res = await fetch(`${API_BASE}/api/tools/github/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }),
  })
  return res.json()
}
export async function githubIssues(owner, repo) {
  const res = await fetch(`${API_BASE}/api/tools/github/issues`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ owner, repo }),
  })
  return res.json()
}
export async function notionSearch(query) {
  const res = await fetch(`${API_BASE}/api/tools/notion/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }),
  })
  return res.json()
}

export async function getToolsStatus() {
  const res = await fetch(`${API_BASE}/api/tools/status`)
  if (!res.ok) throw new Error('Failed to load tools status')
  return res.json()
}

export async function listConversations() {
  return api.listConversations()
}
