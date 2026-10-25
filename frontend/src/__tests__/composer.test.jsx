import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('../catalog.js', () => ({
  exposeModelCatalog: vi.fn().mockResolvedValue({ models: [] }),
  cachedModels: () => [],
}))

import MainPanel from '../MainPanel.jsx'

const baseProps = {
  conversationId: 'conv-a',
  results: null,
  loading: false,
  onSend: vi.fn(),
  onRetry: vi.fn(),
  onCancel: vi.fn(),
  focusSignal: 0,
  defaultMode: 'Balanced',
  selected: [],
  onToggleSelected: () => {},
  webSearch: false,
  onOpenTools: () => {},
}

beforeEach(() => {
  baseProps.onSend = vi.fn()
})

describe('composer', () => {
  it('clears immediately on submit and shows the prompt in the transcript', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<MainPanel {...baseProps} />)
    const box = screen.getByPlaceholderText('Ask CogniSync anything…')

    await user.type(box, 'What is RAG?')
    await user.keyboard('{Enter}')

    expect(baseProps.onSend).toHaveBeenCalledTimes(1)
    expect(baseProps.onSend).toHaveBeenCalledWith({
      question: 'What is RAG?',
      models: [],
      mode: 'Balanced',
      webSearch: false,
    })
    expect(box).toHaveValue('')

    rerender(<MainPanel {...baseProps} loading results={{ question: 'What is RAG?', streaming: true, model_responses: [], discussion: [] }} />)
    expect(screen.getAllByText(/What is RAG\?/).length).toBeGreaterThan(0)
  })

  it('ignores empty submits and never sends the same Enter twice', async () => {
    const user = userEvent.setup()
    render(<MainPanel {...baseProps} />)
    const box = screen.getByPlaceholderText('Ask CogniSync anything…')

    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(baseProps.onSend).not.toHaveBeenCalled()

    await user.type(box, 'Once')
    await user.keyboard('{Enter}{Enter}')
    expect(baseProps.onSend).toHaveBeenCalledTimes(1)
  })

  it('queues follow-ups while running, clearing the composer, then dispatches FIFO on completion', async () => {
    const user = userEvent.setup()
    const running = { question: 'First', streaming: true, model_responses: [], discussion: [] }
    const { rerender } = render(<MainPanel {...baseProps} loading results={running} />)
    const box = screen.getByPlaceholderText(/it queues until the council finishes/)

    await user.type(box, 'Second question')
    await user.keyboard('{Enter}')
    expect(baseProps.onSend).not.toHaveBeenCalled()
    expect(box).toHaveValue('')
    expect(screen.getByText(/Queued #1/)).toBeInTheDocument()

    await user.type(box, 'Third question')
    await user.keyboard('{Enter}')
    expect(screen.getByText(/Queued #2/)).toBeInTheDocument()

    rerender(<MainPanel {...baseProps} loading={false} results={{ ...running, streaming: false }} />)
    await waitFor(() => expect(baseProps.onSend).toHaveBeenCalledTimes(1))
    expect(baseProps.onSend.mock.calls[0][0].question).toBe('Second question')
    expect(screen.getByText(/Queued #1/)).toBeInTheDocument()

    // The App flips loading true while the queued run executes, then false.
    rerender(<MainPanel {...baseProps} loading results={{ ...running, question: 'Second question', streaming: true }} />)
    rerender(<MainPanel {...baseProps} loading={false} results={{ ...running, question: 'Second question', streaming: false }} />)
    await waitFor(() => expect(baseProps.onSend).toHaveBeenCalledTimes(2))
    expect(baseProps.onSend.mock.calls[1][0].question).toBe('Third question')
  })

  it('discards queued drafts when the conversation changes', async () => {
    const user = userEvent.setup()
    const running = { question: 'First', streaming: true, model_responses: [], discussion: [] }
    const { rerender } = render(<MainPanel {...baseProps} loading results={running} />)
    const box = screen.getByPlaceholderText(/it queues until the council finishes/)

    await user.type(box, 'Belongs to conv A')
    await user.keyboard('{Enter}')
    expect(screen.getByText(/Queued #1/)).toBeInTheDocument()

    rerender(<MainPanel {...baseProps} conversationId="conv-b" loading={false} results={{ ...running, streaming: false }} />)

    await waitFor(() => expect(screen.queryByText(/Queued #1/)).not.toBeInTheDocument())
    expect(baseProps.onSend).not.toHaveBeenCalled()
  })
})
