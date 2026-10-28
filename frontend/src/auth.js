const TOKEN_KEY = 'cognisync-token'
const USER_KEY = 'cognisync-user'

export const AUTH_CHANGED = 'cognisync:auth-changed'
export const AUTH_EXPIRED = 'cognisync:auth-expired'

function safeParse(json) {
  try { return JSON.parse(json) } catch { return null }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getStoredUser() {
  return safeParse(localStorage.getItem(USER_KEY) || 'null')
}

export function authHeaders(extra = {}) {
  const token = getToken()
  return token
    ? { ...extra, Authorization: `Bearer ${token}` }
    : { ...extra }
}

// Re-confirm the stored token against the backend (on page refresh).
// Invalid/revoked tokens drop back to the login screen via handleAuthError.
export async function verifyToken() {
  if (!getToken()) return
  try {
    const res = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() })
    if (res.status === 401 || res.status === 403) handleAuthError(res.status)
  } catch { /* backend unreachable: stay on stored state */ }
}

export function setAuth(token, user) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user || null))
  window.dispatchEvent(new Event(AUTH_CHANGED))
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  window.dispatchEvent(new Event(AUTH_CHANGED))
}

// 401/403 handling: drop the stored token once, notify the UI to show the
// login screen. No retries — callers surface the error instead.
export function handleAuthError(status) {
  if (getToken() && (status === 401 || status === 403)) {
    clearAuth()
    window.dispatchEvent(new Event(AUTH_EXPIRED))
    return true
  }
  return false
}

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'

export async function registerUser(username, password) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Registration failed')
  setAuth(data.access_token, { user_id: data.user_id, username: data.username })
  return data
}

export async function loginUser(username, password) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Login failed')
  setAuth(data.access_token, { user_id: data.user_id, username: data.username })
  return data
}

export async function logoutUser() {
  const token = getToken()
  if (token) {
    // Server-side revocation; network failure must not block local logout.
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch { /* offline logout is still a logout */ }
  }
  clearAuth()
}
