import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, beforeEach, describe, it, expect } from 'vitest'

vi.mock('../api.js', () => ({
  listConversations: vi.fn(),
  api: { getConversation: vi.fn() },
}))

import { listConversations, api } from '../api.js'
import HistoryView from '../views/HistoryView.jsx'
import { formatTimestamp } from '../time.js'

const conv = {
  id: 'c1',
  title: 'How do black holes work?',
  updated_at: '2026-10-26T10:30:00Z',
  created_at: '2026-10-25T09:00:00Z',
  message_count: 2,
}

beforeEach(() => {
  vi.clearAllMocks()
  listConversations.mockResolvedValue([conv])
  api.getConversation.mockResolvedValue({
    id: 'c1',
    messages: [
      { role: 'user', content: JSON.stringify({ role: 'user', content: 'Explain black holes' }) },
      { role: 'assistant', final_answer: 'They are **dense** regions.', stage1: [], stage2: [], stage3: { success: true, model: 'm/a' }, sources: [] },
    ],
  })
})

describe('HistoryView', () => {
  it('lists entries collapsed by default with readable title, timestamp and count', async () => {
    render(<HistoryView onContinue={vi.fn()} />)
    expect(await screen.findByText('How do black holes work?')).toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /How do black holes work/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Explain black holes')).not.toBeInTheDocument()
    expect(api.getConversation).not.toHaveBeenCalled()
    expect(screen.getByText(new RegExp(formatTimestamp(conv.updated_at)))).toBeInTheDocument()
    expect(screen.getByText(/2 messages/)).toBeInTheDocument()
  })

  it('expands on click to load saved turns, and collapses again', async () => {
    const user = userEvent.setup()
    render(<HistoryView onContinue={vi.fn()} />)
    const toggle = await screen.findByRole('button', { name: /How do black holes work/ })

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(await screen.findByText('Explain black holes')).toBeInTheDocument()
    expect(screen.getByText('dense')).toBeInTheDocument()
    expect(api.getConversation).toHaveBeenCalledWith('c1')

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Explain black holes')).not.toBeInTheDocument()
  })

  it('continues a conversation without expanding the entry', async () => {
    const user = userEvent.setup()
    const onContinue = vi.fn()
    render(<HistoryView onContinue={onContinue} />)
    const toggle = await screen.findByRole('button', { name: /How do black holes work/ })

    await user.click(screen.getByRole('button', { name: 'Continue in workspace' }))
    expect(onContinue).toHaveBeenCalledWith('c1')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(api.getConversation).not.toHaveBeenCalled()
  })

  it('filters by title and shows an empty state for no matches', async () => {
    const user = userEvent.setup()
    render(<HistoryView onContinue={vi.fn()} />)
    await screen.findByText('How do black holes work?')

    await user.type(screen.getByPlaceholderText('Search conversations...'), 'zzz')
    expect(screen.getByText('No conversations match your search.')).toBeInTheDocument()
  })

  it('shows the empty-history message when the server returns nothing', async () => {
    listConversations.mockResolvedValue([])
    render(<HistoryView onContinue={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/No conversations yet/)).toBeInTheDocument())
  })

  it('shows a retryable error when loading fails', async () => {
    listConversations.mockRejectedValueOnce(new Error('offline'))
    const user = userEvent.setup()
    render(<HistoryView onContinue={vi.fn()} />)
    expect(await screen.findByText(/Could not load conversation history/)).toBeInTheDocument()

    listConversations.mockResolvedValue([conv])
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('How do black holes work?')).toBeInTheDocument()
  })
})
