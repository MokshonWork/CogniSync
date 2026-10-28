import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

import ModelPerformance from '../ModelPerformance.jsx'

const responses = [
  {
    model_id: 'm/a', model: 'Model A', time_ms: 1200, output_tokens: 1200,
    text: 'Complete answer', success: true, state: 'completed',
  },
  {
    model_id: 'm/b', model: 'Model B', time_ms: 800, output_tokens: null,
    text: '', success: false, error: 'rate limit', error_category: 'rate_limit', state: 'failed',
  },
  {
    model_id: 'm/c', model: 'Model C', time_ms: 0, output_tokens: null,
    text: '', success: false, error: 'Request timed out', error_category: 'timeout', state: 'failed',
  },
  {
    model_id: 'm/d', model: 'Model D', time_ms: 40, output_tokens: 0,
    text: '', success: true, state: 'completed',
  },
  {
    model_id: 'm/e', model: 'Model E', time_ms: 0, output_tokens: null,
    text: '', success: null, state: 'running',
  },
]

describe('ModelPerformance', () => {
  it('shows measured time, output tokens, and honest statuses without ranking models', () => {
    render(<ModelPerformance responses={responses} models={[]} />)

    expect(screen.getByRole('heading', { name: 'Model Performance' })).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getAllByText('1.20 s')).toHaveLength(2)
    expect(screen.getAllByText('800 ms')).toHaveLength(2)
    expect(screen.getByText('1,200')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Timed out')).toBeInTheDocument()
    expect(screen.getByText('Incomplete')).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getAllByText('N/A').length).toBeGreaterThanOrEqual(4)
    expect(screen.queryByText(/winner|fastest|best model/i)).not.toBeInTheDocument()
  })

  it('shows loading and empty states instead of inventing metrics', () => {
    const { rerender } = render(<ModelPerformance responses={[]} loading />)
    expect(screen.getByText('Waiting for model results…')).toBeInTheDocument()

    rerender(<ModelPerformance responses={[]} loading={false} />)
    expect(screen.getByText('No model-level metrics were saved for this run.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
