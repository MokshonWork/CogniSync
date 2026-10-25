import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect } from 'vitest'

vi.mock('../api.js', () => ({
  getToolsStatus: vi.fn().mockResolvedValue({ integrations: { web_search: { configured: true } } }),
}))

vi.mock('../catalog.js', () => ({
  useModelCatalog: () => ({ models: [] }),
}))

import RightPanel from '../RightPanel.jsx'
import Markdown from '../Markdown.jsx'
import { mapRunToResults } from '../runResults.js'

describe('Markdown rendering', () => {
  it('renders GFM tables', () => {
    render(<Markdown>{'| Name | Value |\n| --- | --- |\n| alpha | 1 |'}</Markdown>)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('alpha')).toBeInTheDocument()
  })

  it('does not render raw HTML or unsafe link schemes', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <Markdown>{'<script>window.pwned=1</script>\n\n[unsafe](javascript:alert(1))\n\n[safe](https://example.com)'}</Markdown>
    )
    expect(container.querySelector('script')).toBeNull()
    expect(window.pwned).toBeUndefined()

    const unsafe = screen.getByText('unsafe')
    expect(unsafe.getAttribute('href') || '').not.toMatch(/javascript:/i)
    const safe = screen.getByText('safe')
    expect(safe).toHaveAttribute('href', 'https://example.com')
    expect(safe).toHaveAttribute('rel', expect.stringContaining('noopener'))
    await user.click(safe)
  })
})

describe('final-answer contract', () => {
  it('maps final_answer only from the structured field, never by scraping discussion', () => {
    const legacy = mapRunToResults({
      stage1: [{ model: 'm/a', response: 'model text', success: true }],
      stage2: [{ model: 'm/b', success: true, ranking: 'FINAL RANKING:\n1. Response A' }],
      stage3: null,
      metadata: { aggregate_rankings: [] },
    }, 'Q')
    expect(legacy.final_answer).toBeNull()
    expect(legacy.synthesis).toBe('')
    expect(legacy.discussion.at(-1).text).toBe('Chairman synthesis unavailable.')

    const modern = mapRunToResults({
      stage1: [],
      stage2: [],
      stage3: { model: 'm/a', response: 'ignored', success: true },
      final_answer: '# Exact answer',
      metadata: {},
    }, 'Q')
    expect(modern.final_answer).toBe('# Exact answer')
    expect(modern.streaming).toBe(false)
  })

  it('RightPanel teaches the modal only stale-free: a new answer closes the old modal', async () => {
    const user = userEvent.setup()
    const base = {
      streaming: false,
      final_answer: '# Answer A',
      final_answer_error: null,
      model_responses: [],
      sources: [],
      search: { enabled: false },
    }
    const { rerender } = render(
      <RightPanel results={base} webSearch={false} onToggleWebSearch={() => {}} selectedModels={[]} onOpenTools={() => {}} onRetry={() => {}} />
    )

    await user.click(screen.getByRole('button', { name: /View full answer/ }))
    expect(screen.getByRole('dialog', { name: 'Full final answer' })).toBeInTheDocument()
    expect(screen.getAllByText('Answer A').length).toBeGreaterThan(0)

    rerender(
      <RightPanel
        results={{ ...base, final_answer: '# Answer B' }}
        webSearch={false}
        onToggleWebSearch={() => {}}
        selectedModels={[]}
        onOpenTools={() => {}}
        onRetry={() => {}}
      />
    )
    expect(screen.queryByRole('dialog', { name: 'Full final answer' })).not.toBeInTheDocument()
    expect(screen.getByText('Answer B')).toBeInTheDocument()
  })

  it('shows synthesis failure with retry and never falls back to discussion text', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(
      <RightPanel
        results={{
          streaming: false,
          final_answer: null,
          final_answer_error: 'Chairman timed out',
          model_responses: [],
          discussion: [{ type: 'Synthesis', text: 'SCRAPED DISCUSSION SNIPPET' }],
          sources: [],
        }}
        webSearch={false}
        onToggleWebSearch={() => {}}
        selectedModels={[]}
        onOpenTools={() => {}}
        onRetry={onRetry}
      />
    )
    expect(screen.getByText(/Chairman timed out/)).toBeInTheDocument()
    expect(screen.queryByText(/SCRAPED DISCUSSION SNIPPET/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalled()
  })
})
