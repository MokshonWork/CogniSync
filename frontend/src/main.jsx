import { StrictMode, Component } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// A render failure in one panel must not blank the whole app.
class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-3 bg-canvas p-8 font-sans text-ink">
          <h1 className="font-display text-2xl font-semibold">Something went wrong rendering the UI</h1>
          <pre className="max-w-xl overflow-auto whitespace-pre-wrap rounded-xl bg-white p-4 text-[12px] text-neutral-500">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <div className="flex gap-2">
            <button
              onClick={() => this.setState({ error: null })}
              className="rounded-lg bg-ink px-4 py-2 text-[13px] font-medium text-white"
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="rounded-lg border border-black/10 px-4 py-2 text-[13px] text-neutral-600"
            >
              Reload page
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
