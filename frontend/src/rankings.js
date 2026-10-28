// Peer-review rankings come from the backend's real Stage 2 data:
// metadata.aggregate_rankings, metadata.label_to_model, and stage2[].parsed_ranking.
// Lower average_rank is better. A winner is declared only for a unique top rank.
export function rankingOutcome(aggregateRankings = [], peerReviews = []) {
  const rows = (Array.isArray(aggregateRankings) ? aggregateRankings : [])
    .filter((r) => r && Number.isFinite(r.average_rank) && r.rankings_count > 0)
    .map((r) => ({
      model_id: r.model_id || r.model,
      model: r.model || r.model_id || 'Unknown model',
      average_rank: r.average_rank,
      rankings_count: r.rankings_count,
    }))
    .sort((a, b) => a.average_rank - b.average_rank)

  if (!rows.length) {
    return { rows: [], winner: null, leaders: [], tied: false, firstPlaceVotes: 0, reviewCount: 0 }
  }

  const best = rows[0].average_rank
  const leaders = rows.filter((r) => r.average_rank === best)
  const winner = leaders.length === 1 ? leaders[0] : null
  const reviews = (Array.isArray(peerReviews) ? peerReviews : [])
    .filter((r) => r?.success && Array.isArray(r.parsed_ranking) && r.parsed_ranking.length)
  const firstPlaceVotes = winner
    ? reviews.filter((r) => r.first_place_model_id === winner.model_id).length
    : 0

  return {
    rows,
    winner,
    leaders,
    tied: leaders.length > 1,
    firstPlaceVotes,
    reviewCount: reviews.length,
  }
}

export function rankBarWidth(averageRank, rows) {
  const best = Math.min(...rows.map((r) => r.average_rank))
  return Math.max(8, Math.round((best / averageRank) * 100))
}
