import { describe, it, expect, beforeEach, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('auth state handling', () => {
  it('stores and clears the token and user', async () => {
    const { setAuth, getToken, getStoredUser, clearAuth } = await import('../auth.js')

    setAuth('tok-123', { user_id: 'u1', username: 'tester' })
    expect(getToken()).toBe('tok-123')
    expect(getStoredUser()).toEqual({ user_id: 'u1', username: 'tester' })

    clearAuth()
    expect(getToken()).toBe('')
    expect(getStoredUser()).toBeNull()
  })

  it('authHeaders adds the Bearer token when present', async () => {
    const { setAuth, authHeaders, clearAuth } = await import('../auth.js')

    setAuth('tok-123', { user_id: 'u1', username: 'tester' })
    expect(authHeaders({ 'X-A': 1 })).toEqual({ 'X-A': 1, Authorization: 'Bearer tok-123' })

    clearAuth()
    expect(authHeaders({ 'X-A': 1 })).toEqual({ 'X-A': 1 })
  })

  it('handleAuthError clears the token and fires the expired event on 401/403', async () => {
    const { setAuth, handleAuthError, getToken } = await import('../auth.js')
    const fired = vi.fn()
    window.addEventListener('cognisync:auth-expired', fired)
    window.addEventListener('cognisync:auth-changed', fired)

    setAuth('tok-123', { user_id: 'u1', username: 'tester' })
    expect(handleAuthError(500)).toBe(false)
    expect(getToken()).toBe('tok-123')

    expect(handleAuthError(401)).toBe(true)
    expect(getToken()).toBe('')
    expect(handleAuthError(403)).toBe(false) // nothing stored anymore
    expect(fired).toHaveBeenCalledTimes(3) // auth-changed + auth-expired + second auth-changed
  })

  it('login submits credentials and stores the token', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: 'jwt-x', user_id: 'u1', username: 'tester', expires_in: 3600 }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const { loginUser, getToken, getStoredUser } = await import('../auth.js')
    await loginUser('tester', 'pw123456')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8001/auth/login',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'tester', password: 'pw123456' }) },
    )
    expect(getToken()).toBe('jwt-x')
    expect(getStoredUser().username).toBe('tester')
  })

  it('login failure surfaces the backend detail', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Invalid username or password' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const { loginUser, getToken } = await import('../auth.js')
    await expect(loginUser('tester', 'bad')).rejects.toThrow('Invalid username or password')
    expect(getToken()).toBe('')
  })
})
