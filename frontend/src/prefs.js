// Local, non-sensitive UI preferences. Never store credentials here.
const KEY = 'cognisync-prefs'

export const DEFAULT_PREFS = {
  mode: 'Balanced',
  // Empty selection = "use all catalog-verified free models" — real model
  // ids come from the backend catalog, never hardcoded client-side.
  selectedModels: [],
  webSearch: false,
  collapseSidebar: false,
}

export function loadPrefs() {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || '{}')
    // Coerce to safe types: older versions may have persisted wrong shapes.
    if (!Array.isArray(stored.selectedModels)) stored.selectedModels = []
    if (typeof stored.webSearch !== 'boolean') stored.webSearch = DEFAULT_PREFS.webSearch
    if (typeof stored.mode !== 'string') delete stored.mode
    return { ...DEFAULT_PREFS, ...stored }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function savePrefs(p) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p))
  } catch {
    // storage unavailable (private mode) — prefs stay session-only
  }
}
