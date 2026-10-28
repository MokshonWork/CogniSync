import { useState } from 'react'
import { Icon } from '../icons.jsx'

// Built-in prompts are static; user prompts persist in localStorage only
// (no prompt-storage backend exists — nothing here claims server sync).
const BUILT_IN = [
  { id: 'b1', category: 'Analysis', title: 'Compare two approaches', text: 'Compare the main approaches to solve a problem, listing strengths, weaknesses, trade-offs, assumptions, and the situations in which each approach is the better choice. End with a concise comparison table and a recommendation based on the stated priorities.' },
  { id: 'b2', category: 'Decision', title: 'Analyze a trade-off', text: 'I need to decide between [option A] and [option B]. Compare them against my goals, constraints, cost, implementation effort, risks, reversibility, and long-term consequences. Identify the information that could change the decision, then recommend one option with a clear rationale. If critical context is missing, state your assumptions.' },
  { id: 'b3', category: 'Reasoning', title: 'Challenge assumptions', text: 'Here is my argument: [argument]. Identify its explicit and hidden assumptions, examine the evidence supporting each major claim, find logical gaps and counterexamples, and present the strongest opposing argument. Distinguish facts from speculation and suggest how the argument could be improved.' },
  { id: 'b4', category: 'Writing', title: 'Sharpen writing', text: 'Rewrite the following text to make it clearer, more concise, and better structured without changing its intended meaning: [text]. Preserve important details, use natural language, remove unnecessary repetition, and briefly explain any substantial changes.' },
  { id: 'b5', category: 'Code', title: 'Code review', text: 'Review the following code for correctness, security, edge cases, performance, maintainability, and simpler alternatives: [code]. Prioritize findings by severity, explain why each issue matters, and suggest concrete fixes. Do not invent bugs; distinguish confirmed issues from potential risks. If the code is sound, say so and recommend useful tests.' },
]

const CATEGORIES = ['All', 'Analysis', 'Decision', 'Reasoning', 'Writing', 'Code', 'My prompts']
const USER_KEY = 'cognisync-user-prompts'

function loadUserPrompts() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || '[]') } catch { return [] }
}

export default function LibraryView({ onUsePrompt }) {
  const [userPrompts, setUserPrompts] = useState(loadUserPrompts)
  const [search, setSearch] = useState('')
  const [cat, setCat] = useState('All')
  const [newTitle, setNewTitle] = useState('')
  const [newText, setNewText] = useState('')

  const persist = (list) => {
    setUserPrompts(list)
    try { localStorage.setItem(USER_KEY, JSON.stringify(list)) } catch { /* session-only */ }
  }

  const match = (p) =>
    (cat === 'All' || (cat === 'My prompts' ? p.user : p.category === cat)) &&
    (p.title + ' ' + p.text).toLowerCase().includes(search.toLowerCase())

  const built = BUILT_IN.filter(match)
  const mine = userPrompts.filter(match)

  return (
    <section className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="font-display text-[28px] font-semibold text-ink">Prompt library</h1>
      <p className="text-[13px] text-neutral-400">Built-in prompts plus your own, stored in this browser only.</p>

      <div className="mt-5 flex flex-wrap gap-2">
        {CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => setCat(c)}
            className={`rounded-full px-3 py-1.5 text-[12px] transition-colors ${cat === c ? 'bg-ink text-white' : 'border border-black/10 bg-white text-neutral-600 hover:border-black/30'}`}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="relative mt-4">
        <Icon name="search" size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search prompts..."
          className="w-full rounded-xl border border-black/10 bg-white py-2.5 pl-9 pr-3 text-[14px] outline-none focus:border-black/30"
        />
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {built.length + mine.length === 0 && (
          <div className="rounded-xl border border-black/10 bg-white p-6 text-center text-[13px] text-neutral-500">
            No prompts match. Create one below.
          </div>
        )}
        {[...mine.map((p) => ({ ...p, user: true })), ...built].map((p) => (
          <div key={p.id} className="rounded-xl border border-black/5 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="label-xs rounded-full bg-black/5 px-2 py-0.5 text-neutral-500">{p.user ? 'My prompt' : p.category}</span>
                  <span className="truncate text-[13px] font-medium text-ink">{p.title}</span>
                </div>
                <p className="mt-1 truncate text-[12px] text-neutral-400">{p.text}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  onClick={() => onUsePrompt(p.text)}
                  className="rounded-lg bg-ink px-3 py-1.5 text-[12px] font-medium text-white hover:bg-black"
                >
                  Use prompt
                </button>
                {p.user && (
                  <button
                    onClick={() => persist(userPrompts.filter((x) => x.id !== p.id))}
                    className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-neutral-500 hover:border-red-300 hover:text-red-500"
                    aria-label={`Delete ${p.title}`}
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* create user prompt */}
      <div className="mt-5 rounded-xl border border-black/10 bg-white p-4">
        <div className="label-xs text-neutral-400">Create prompt</div>
        <input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="Title"
          className="mt-2 w-full rounded-lg border border-black/10 px-3 py-2 text-[13px] outline-none focus:border-black/30"
        />
        <textarea
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          placeholder="Prompt text..."
          rows={3}
          className="mt-2 w-full resize-y rounded-lg border border-black/10 px-3 py-2 text-[13px] outline-none focus:border-black/30"
        />
        <button
          onClick={() => {
            if (!newTitle.trim() || !newText.trim()) return
            persist([{ id: crypto.randomUUID(), title: newTitle.trim(), text: newText.trim(), user: true }, ...userPrompts])
            setNewTitle(''); setNewText('')
          }}
          disabled={!newTitle.trim() || !newText.trim()}
          className="mt-2 rounded-lg bg-ink px-4 py-2 text-[12px] font-medium text-white disabled:opacity-30"
        >
          Save prompt
        </button>
        <p className="mt-1.5 text-[11px] text-neutral-400">Saved locally in this browser — not synced to the server.</p>
      </div>
    </section>
  )
}
