import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('../api.js', () => ({
  api: { getConversation: vi.fn() },
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
import { runCouncilStream } from '../api.js'

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.setItem('cognisync-token', 'test-token')
  localStorage.setItem('cognisync-user', JSON.stringify({ user_id: 'u1', username: 'tester' }))
  runCouncilStream.mockReset()
})

describe('queued prompts and sessions', () => {
  it('drops a queued prompt when starting a new session and never sends it', async () => {
    let finishRun
    runCouncilStream.mockImplementation(() => new Promise((resolve) => { finishRun = resolve }))
    const user = userEvent.setup()

    render(<App />)
    const box = screen.getByPlaceholderText('Ask CogniSync anything…')

    await user.type(box, 'First question')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(runCouncilStream).toHaveBeenCalledTimes(1))

    const queuedBox = screen.getByPlaceholderText(/it queues until the council finishes/)
    await user.type(queuedBox, 'Belongs to the old session')
    await user.keyboard('{Enter}')
    expect(screen.getByText(/Queued #1/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'New session' }))

    await waitFor(() => expect(screen.queryByText(/Queued #1/)).not.toBeInTheDocument())
    await act(async () => { finishRun() })
    await waitFor(() => expect(runCouncilStream).toHaveBeenCalledTimes(1))
  })
})
