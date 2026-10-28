import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, beforeEach, afterEach, describe, it, expect } from 'vitest'

vi.mock('../api.js', () => ({
  api: { getConversation: vi.fn(), deleteConversation: vi.fn() },
  runCouncilStream: vi.fn(),
  listConversations: vi.fn().mockResolvedValue([]),
  getToolsStatus: vi.fn().mockResolvedValue({ integrations: {} }),
}))

vi.mock('../catalog.js', () => ({
  exposeModelCatalog: vi.fn().mockResolvedValue({ models: [] }),
  cachedModels: () => [],
  useModelCatalog: () => ({ models: [] }),
}))

import App from '../App.jsx'
import { api, listConversations, runCouncilStream } from '../api.js'

const CURRENT_ID = '00000000-0000-4000-8000-000000000000'
const conversation = {
  id: CURRENT_ID,
  title: 'Current session',
  updated_at: '2026-10-26T10:30:00Z',
  created_at: '2026-10-25T09:00:00Z',
  message_count: 2,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(crypto, 'randomUUID').mockReturnValue(CURRENT_ID)
  localStorage.setItem('cognisync-token', 'test-token')
  localStorage.setItem('cognisync-user', JSON.stringify({ user_id: 'u1', username: 'tester' }))
  listConversations.mockResolvedValue([conversation])
  runCouncilStream.mockResolvedValue()
  api.deleteConversation.mockResolvedValue()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('conversation deletion', () => {
  it('confirms, deletes the current session, and resets the workspace', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByPlaceholderText('Ask CogniSync anything…'), 'First question')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(runCouncilStream).toHaveBeenCalledTimes(1))
    expect(screen.getByText('First question')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete Current session' }))
    expect(screen.getByRole('dialog', { name: 'Delete conversation?' })).toBeInTheDocument()
    expect(screen.getByText(/permanently deleted/)).toHaveTextContent('Current session')

    await user.click(screen.getByRole('button', { name: 'Delete conversation' }))

    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith(CURRENT_ID))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: /Let's think this through/ })).toBeInTheDocument()
    expect(screen.queryByText('First question')).not.toBeInTheDocument()
  })

  it('cancels without deleting anything', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'History' }))
    const history = (await screen.findByRole('heading', { name: 'History' })).closest('section')
    await user.click(within(history).getByRole('button', { name: 'Delete Current session' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(api.deleteConversation).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(within(history).getByText('Current session')).toBeInTheDocument()
  })

  it('keeps the conversation and reports an error when deletion fails', async () => {
    const user = userEvent.setup()
    api.deleteConversation.mockRejectedValue(new Error('backend unavailable'))
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'History' }))
    const history = (await screen.findByRole('heading', { name: 'History' })).closest('section')
    const row = within(history).getByRole('button', { name: 'Delete Current session' })
    await user.click(row)
    await user.click(screen.getByRole('button', { name: 'Delete conversation' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('backend unavailable')
    expect(screen.getByRole('dialog', { name: 'Delete conversation?' })).toBeInTheDocument()
    expect(within(history).getByText('Current session')).toBeInTheDocument()
  })
})
