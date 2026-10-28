import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

import ModelComparison from '../ModelComparison.jsx'
import { mapRunToResults } from '../runResults.js'

const responses = [
  { model_id: 'm/a', model: 'Model A', text: 'Answer A', success: true, state: 'completed' },
  { model_id: 'm/b', model: 'Model B', text: 'Answer B', success: true, state: 'completed' },
  { model_id: 'm/c', model: 'Model C', success: false, error: 'rate limit', state: 'failed' },
]

const aggregate = [
  { model_id: 'm/a', model: 'Model A', average_rank: 1, rankings_count: 2 },
  { model_id: 'm/b', model: 'Model B', average_rank: 2, rankings_count: 2 },
]

describe('ModelComparison', () => {
  it('renders the real average ranks and declares the unique top-ranked model', () => {
    render(
      <ModelComparison
        responses={responses}
        aggregateRankings={aggregate}
        peerReviews={[
          { success: true, parsed_ranking: ['Response A', 'Response B'], first_place_model_id: 'm/a' },
          { success: true, parsed_ranking: ['Response A', 'Response B'], first_place_model_id: 'm/a' },
        ]}
      />
    )

    expect(screen.getByRole('heading', { name: 'Model Comparison' })).toBeInTheDocument()
    expect(screen.getByText('Top-ranked by peer review')).toBeInTheDocument()
    expect(screen.getAllByText(/avg rank 1/).length).toBeGreaterThan(0)
    expect(screen.getByText(/2 first-place votes/)).toBeInTheDocument()
    expect(screen.queryByText(/Tied for top rank/)).not.toBeInTheDocument()
  })

  it('does not invent a winner when ranking data is missing', () => {
    render(<ModelComparison responses={responses} aggregateRankings={[]} />)

    expect(screen.getByText(/Peer ranking scores are unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/no winner is declared/)).toBeInTheDocument()
    expect(screen.getAllByText('Completed')).toHaveLength(2)
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('rate limit')).toBeInTheDocument()
    expect(screen.queryByText('Top-ranked by peer review')).not.toBeInTheDocument()
  })

  it('separates ranked models from partial and failed responses', () => {
    render(<ModelComparison responses={responses} aggregateRankings={[aggregate[0]]} />)

    expect(screen.getByText('Top-ranked by peer review')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Not ranked' })).toBeInTheDocument()
    expect(screen.getByText('Model B')).toBeInTheDocument()
    expect(screen.getByText('Model C')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.queryByText(/avg rank 2/)).not.toBeInTheDocument()
  })

  it('reports a tie instead of choosing one winner', () => {
    const tied = [
      { model_id: 'm/a', model: 'Model A', average_rank: 1.5, rankings_count: 2 },
      { model_id: 'm/b', model: 'Model B', average_rank: 1.5, rankings_count: 2 },
    ]
    render(<ModelComparison responses={responses} aggregateRankings={tied} />)

    expect(screen.getByText(/Tied for top rank/)).toBeInTheDocument()
    expect(screen.getByText(/No single winner is declared/)).toBeInTheDocument()
    expect(screen.queryByText('Top-ranked by peer review')).not.toBeInTheDocument()
  })

  it('shows configured criteria when the backend provides them', () => {
    render(
      <ModelComparison
        responses={responses}
        aggregateRankings={aggregate}
        criteria={[{ id: 'relevance', label: 'Relevance' }, { id: 'clarity', label: 'Clarity' }]}
      />
    )

    expect(screen.getByText(/Criteria: Relevance, Clarity/)).toBeInTheDocument()
    expect(screen.queryByText(/does not report per-criterion scores/)).not.toBeInTheDocument()
  })

  it('shows loading and empty states', () => {
    const { rerender } = render(<ModelComparison responses={[]} loading />)
    expect(screen.getByText('Waiting for model responses and peer rankings…')).toBeInTheDocument()

    rerender(<ModelComparison responses={responses} aggregateRankings={[]} loading />)
    expect(screen.getByText(/Peer rankings are still being collected/)).toBeInTheDocument()
    expect(screen.queryByText(/scores are unavailable for this run/)).not.toBeInTheDocument()

    rerender(<ModelComparison responses={[]} loading={false} />)
    expect(screen.getByText('No model responses were recorded for this run.')).toBeInTheDocument()
  })
})

describe('runResults ranking mapping', () => {
  it('maps real aggregate rankings and first-place review evidence', () => {
    const mapped = mapRunToResults({
      stage1: [
        { model: 'm/a', model_id: 'm/a', response: 'A', success: true },
        { model: 'm/b', model_id: 'm/b', response: 'B', success: true },
      ],
      stage2: [{
        model: 'm/a',
        model_id: 'm/a',
        success: true,
        ranking: 'FINAL RANKING:\n1. Response B\n2. Response A',
        parsed_ranking: ['Response B', 'Response A'],
      }],
      stage3: { model: 'm/a', response: 'final', success: true },
      metadata: {
        label_to_model: { 'Response A': 'm/a', 'Response B': 'm/b' },
        aggregate_rankings: [
          { model: 'm/b', average_rank: 1, rankings_count: 1 },
          { model: 'm/a', average_rank: 2, rankings_count: 1 },
        ],
      },
    }, 'Q')

    expect(mapped.aggregate_rankings).toEqual([
      { model_id: 'm/b', model: 'm/b', average_rank: 1, rankings_count: 1 },
      { model_id: 'm/a', model: 'm/a', average_rank: 2, rankings_count: 1 },
    ])
    expect(mapped.peer_reviews[0].first_place_model_id).toBe('m/b')
    expect(mapped.evaluation_criteria).toEqual([])
  })
})
