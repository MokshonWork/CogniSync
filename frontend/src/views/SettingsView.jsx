import { useModelCatalog } from '../catalog.js'
import { familyIcon } from '../icons.jsx'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'

export default function SettingsView({ prefs, onPrefsChange }) {
  const set = (patch) => onPrefsChange({ ...prefs, ...patch })
  const { models, loading, error } = useModelCatalog()

  return (
    <section className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="font-display text-[28px] font-semibold text-ink">Settings</h1>
      <p className="text-[13px] text-neutral-400">
        Saved in this browser (localStorage). Nothing sensitive is stored — no API keys, no credentials.
      </p>

      <div className="mt-6 flex flex-col gap-4">
        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[14px] font-semibold text-ink">Default response mode</div>
              <p className="text-[12px] text-neutral-400">Applied to new sessions.</p>
            </div>
            <select
              value={prefs.mode}
              onChange={(e) => set({ mode: e.target.value })}
              className="rounded-lg border border-black/10 px-3 py-1.5 text-[13px]"
            >
              <option>Balanced</option>
              <option>Fast</option>
            </select>
          </div>
        </div>

        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="text-[14px] font-semibold text-ink">Default council members</div>
          <p className="text-[12px] text-neutral-400">Models that answer new requests.</p>
          {loading && <p className="text-[12px] text-neutral-400">Loading catalog…</p>}
          {error && <p className="text-[12px] text-red-500">{error}</p>}
          <div className="mt-3 grid grid-cols-2 gap-2">
            {models.map((m) => {
              const on = prefs.selectedModels.length
                ? prefs.selectedModels.includes(m.key)
                : true // empty selection = all catalog free models
              const MIcon = familyIcon(m.family)
              return (
                <label key={m.key} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 ${on ? 'border-black/20' : 'border-black/5 opacity-60'}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      set({
                        selectedModels: on
                          ? prefs.selectedModels.filter((k) => k !== m.key)
                          : [...prefs.selectedModels, m.key],
                      })
                    }
                    className="h-3.5 w-3.5 accent-black"
                  />
                  <span className="flex h-6 w-6 items-center justify-center rounded-md" style={{ background: `${m.color}18`, color: m.color }}>
                    <MIcon size={13} />
                  </span>
                  <span className="min-w-0 truncate text-[12px] text-ink">{m.name}</span>
                </label>
              )
            })}
          </div>
          <p className="mt-2 text-[11px] text-neutral-400">An empty selection means every free-eligible model from the OpenRouter catalog is used.</p>
        </div>

        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[14px] font-semibold text-ink">Web search by default</div>
              <p className="text-[12px] text-neutral-400">Uses library-based DuckDuckGo search — no API key required.</p>
            </div>
            <button
              onClick={() => set({ webSearch: !prefs.webSearch })}
              className={`relative h-5 w-9 rounded-full transition-colors ${prefs.webSearch ? 'bg-gpt' : 'bg-black/10'}`}
              aria-label="Toggle default web search"
            >
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${prefs.webSearch ? 'left-[18px]' : 'left-0.5'}`} />
            </button>
          </div>
        </div>

        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[14px] font-semibold text-ink">Collapse sidebar</div>
              <p className="text-[12px] text-neutral-400">Current: {prefs.collapseSidebar ? 'collapsed' : 'expanded'} (the toggle button also works any time).</p>
            </div>
            <button
              onClick={() => set({ collapseSidebar: !prefs.collapseSidebar })}
              className={`relative h-5 w-9 rounded-full transition-colors ${prefs.collapseSidebar ? 'bg-gpt' : 'bg-black/10'}`}
              aria-label="Toggle sidebar collapse preference"
            >
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${prefs.collapseSidebar ? 'left-[18px]' : 'left-0.5'}`} />
            </button>
          </div>
        </div>

        <div className="rounded-2xl border border-black/5 bg-white p-4">
          <div className="text-[14px] font-semibold text-ink">Application information</div>
          <ul className="mt-2 flex flex-col gap-1 text-[12px] text-neutral-500">
            <li>CogniSync — web workspace ({API_BASE.replace(/^https?:\/\//, '')} backend)</li>
            <li>Authentication: none (single-user local deployment)</li>
            <li>Backend storage: JSON files in <code className="rounded bg-black/5 px-1">data/conversations/</code></li>
            <li>Prompt library and preferences: browser-local only</li>
          </ul>
        </div>
      </div>
    </section>
  )
}
