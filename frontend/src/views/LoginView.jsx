import { useState } from 'react'
import { loginUser, registerUser } from '../auth.js'

export default function LoginView() {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setError(null)
    setBusy(true)
    try {
      if (mode === 'register') await registerUser(username.trim(), password)
      else await loginUser(username.trim(), password)
      // App.jsx observes `cognisync:auth-changed` and re-renders signed in.
    } catch (err) {
      setError(err.message || 'Something went wrong — try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6 font-sans">
      <div className="w-full max-w-sm rounded-2xl border border-black/5 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink">Multiple Minds. One Clear Answer.</h1>
        <p className="mt-1 text-sm text-neutral-500">
          {mode === 'login'
            ? 'Sign in to your CogniSync account.'
            : 'Create an account to start asking the council.'}
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="auth-username" className="block text-[12px] font-medium text-neutral-600">
              Username
            </label>
            <input
              id="auth-username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm outline-none focus:border-ink/40"
            />
          </div>
          <div>
            <label htmlFor="auth-password" className="block text-[12px] font-medium text-neutral-600">
              Password
            </label>
            <input
              id="auth-password"
              type="password"
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm outline-none focus:border-ink/40"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-[12px] text-red-600" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="w-full rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        <button
          type="button"
          onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null) }}
          className="mt-4 text-[12px] text-neutral-500 underline"
        >
          {mode === 'login' ? 'No account yet? Register' : 'Already have an account? Sign in'}
        </button>
      </div>
    </div>
  )
}
