import { useState, useEffect, useCallback } from 'react'
import { Icon } from './icons.jsx'
import { listConversations } from './api.js'
import { toDate } from './time.js'
import logo from '../public/assets/cognisynce_logo.png'
import avatar from '../public/assets/user_avatar.png'

const NAV = [null, 'history', 'library', 'models', 'compare', 'tools', 'settings']
const SECTIONS = ['New session', 'History', 'Prompt library', 'Model settings', 'Comparisons', 'Tools', 'Settings']

function fmtDate(iso) {
  const d = toDate(iso)
  return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''
}

export default function Sidebar({ collapsed, onToggle, onNewSession, onViewChange, view, onOpenConversation, onDelete, user, onLogout }) {
  const [recents, setRecents] = useState([])

  const refresh = useCallback(() => {
    listConversations()
      .then((list) => setRecents(list
        .filter((c) => c.message_count > 0)
        .slice(0, 5)
        .map((c) => ({ id: c.id, title: c.title, date: fmtDate(c.updated_at || c.created_at) }))))
      .catch(() => setRecents([]))
  }, [])

  useEffect(() => {
    refresh()
    const onChanged = () => refresh()
    window.addEventListener('cognisync:conversations-changed', onChanged)
    return () => window.removeEventListener('cognisync:conversations-changed', onChanged)
  }, [refresh])

  return (
    <aside
      className={`flex h-screen flex-col bg-sidebar transition-all duration-200 ${collapsed ? 'w-16' : 'w-[260px]'} shrink-0 border-r border-black/5`}
    >
      <div className="flex items-center justify-between px-4 pt-5">
        {!collapsed && (
          <button
            onClick={() => onViewChange('chat')}
            className="flex items-center gap-2 font-display text-lg font-semibold text-ink"
            title="Back to workspace"
          >
            <img src={logo} alt="CogniSync" className="h-6 w-6 object-contain" />
            CogniSync
          </button>
        )}
        <button
          onClick={onToggle}
          className="rounded-md p-1.5 text-neutral-400 transition-colors hover:bg-black/5 hover:text-ink"
          aria-label="Toggle sidebar"
        >
          <Icon name="panelRight" size={16} />
        </button>
      </div>

      <nav className="mt-6 flex flex-col gap-1 px-3">
        {SECTIONS.map((label, i) => {
          const isNew = i === 0
          const target = NAV[i]
          const active = !isNew && typeof view === 'string' && view === target
          return (
            <button
              key={label}
              onClick={isNew ? onNewSession : () => onViewChange(target)}
              title={collapsed ? label : undefined}
              aria-current={active ? 'page' : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] transition-colors ${
                active
                  ? 'bg-black/10 font-medium text-ink'
                  : isNew
                    ? 'bg-ink font-medium text-white hover:bg-black'
                    : 'text-neutral-600 hover:bg-black/5 hover:text-ink'
              }`}
            >
              <Icon name={NAV[i] === 'tools' ? 'wrench' : NAV[i]} size={15} className={collapsed ? 'mx-auto' : 'shrink-0'} />
              {!collapsed && label}
            </button>
          )
        })}
      </nav>

      {!collapsed && (
        <>
          <div className="mt-8 px-4">
            <div className="flex items-center justify-between">
              <span className="label-xs text-neutral-400">Recent</span>
              <button onClick={() => onViewChange('history')} className="text-neutral-400 hover:text-ink" aria-label="Search sessions">
                <Icon name="search" size={13} />
              </button>
            </div>
            <ul className="mt-2 flex flex-col gap-0.5">
              {recents.length === 0 && (
                <li className="py-1 text-[12px] text-neutral-400">No sessions yet</li>
              )}
              {recents.map((r) => (
                <li key={r.id} className="group flex items-center gap-1">
                  <button
                    onClick={() => onOpenConversation(r.id)}
                    className="flex min-w-0 flex-1 items-center justify-between rounded-md px-2 py-1.5 hover:bg-black/5 text-left"
                    title={r.title}
                  >
                    <span className="min-w-0 flex-1 truncate text-[13px] text-neutral-600">{r.title}</span>
                    <span className="ml-2 shrink-0 text-[11px] text-neutral-400">{r.date}</span>
                  </button>
                  {onDelete && (
                    <button
                      type="button"
                      onClick={() => onDelete({ id: r.id, title: r.title })}
                      aria-label={`Delete ${r.title}`}
                      title="Delete conversation"
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-neutral-400 hover:bg-red-50 hover:text-red-500 lg:h-8 lg:w-8"
                    >
                      <Icon name="trash" size={12} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-auto mb-4 flex items-center gap-3 px-4">
            <div className="relative shrink-0">
              <img src={avatar} alt={user?.username || 'Account'} className="h-8 w-8 rounded-full object-cover" />
              <span className="absolute -bottom-0 -right-0 h-2.5 w-2.5 rounded-full border-2 border-sidebar bg-gpt" />
            </div>
            <div className="flex-1">
              <div className="truncate text-[13px] font-medium text-ink">{user?.username || 'Account'}</div>
              <div className="text-[11px] text-neutral-400">Signed in</div>
            </div>
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                className="label-xs rounded-full border border-black/10 px-2 py-0.5 text-neutral-500 hover:bg-black/5 hover:text-ink"
                title="Sign out"
              >
                Log out
              </button>
            )}
          </div>
        </>
      )}
    </aside>
  )
}
