// Pure mapping helpers shared by App.jsx, HistoryView and CompareView.

// Build the UI-facing results shape from the backend's authoritative final
// payload (same contract for the blocking path and history hydration).
export function mapRunToResults(payload, fallbackQuestion) {
  const { stage1 = [], stage2 = [], stage3 = null, metadata = {}, sources = null } = payload
  const displayName = {}
  for (const s of stage1) {
    if (s.model_id) displayName[s.model_id] = s.model
    displayName[s.model] = s.model
  }

  const labelToModel = metadata.label_to_model || {}
  const agg = (Array.isArray(metadata.aggregate_rankings) ? metadata.aggregate_rankings : [])
    .filter((a) => a && Number.isFinite(a.average_rank) && a.rankings_count > 0)
    .map((a) => ({
      model_id: a.model,
      model: displayName[a.model] || a.model,
      average_rank: a.average_rank,
      rankings_count: a.rankings_count,
    }))

  const peerReviews = (Array.isArray(stage2) ? stage2 : []).map((r) => {
    const parsed = Array.isArray(r.parsed_ranking) ? r.parsed_ranking : []
    return {
      model_id: r.model_id || r.model,
      model: displayName[r.model] || r.model,
      success: r.success === true,
      ranking: r.ranking || '',
      parsed_ranking: parsed,
      first_place_model_id: parsed[0] ? (labelToModel[parsed[0]] || null) : null,
    }
  })

  const modelResponses = stage1.map((s) => ({
    model: s.model,
    model_id: s.model_id || s.model,
    time_ms: s.duration_ms || 0,
    tokens: s.tokens || 0,
    output_tokens: Number.isFinite(s.output_tokens) ? s.output_tokens : null,
    text: s.response || '',
    success: s.success,
    error: s.error || null,
    error_category: s.error_category || null,
    error_original: s.error_original || null,
    fallback_used: s.fallback_used || null,
    fallback_display: s.fallback_display || null,
    state: s.success ? 'completed' : 'failed',
  }))

  const successCount = modelResponses.filter((m) => m.success).length
  const allFailed = successCount === 0 && modelResponses.length > 0
  const allQuota = allFailed && modelResponses.every((m) => (m.error_original || '').includes('quota')
    || (m.error || '').includes('free-tier daily'))

  const top = agg.find((a) => a.rankings_count) || null
  const topName = top ? (displayName[top.model] || top.model) : null
  const reviews = Array.isArray(stage2) ? stage2.filter((r) => r.success && r.ranking) : []

  const discussion = []
  if (topName) {
    discussion.push({
      type: 'Agreement',
      text: `${top.rankings_count} peer review${top.rankings_count > 1 ? 's' : ''} ranked ${topName} first (avg position ${top.average_rank}).`,
    })
  } else if (allQuota) {
    discussion.push({
      type: 'Agreement',
      text: `0 of ${modelResponses.length} council models responded — the OpenRouter free-tier daily quota (per-account) is exhausted. It resets at UTC midnight; retry after the reset or add credits to raise the limit.`,
    })
  } else if (allFailed) {
    discussion.push({
      type: 'Agreement',
      text: `0 of ${modelResponses.length} council models responded — all were rate-limited or failed. Transient limits were automatically retried; individual errors are on each card.`,
    })
  } else if (successCount > 0 && !stage2?.length && stage3?.success === false) {
    discussion.push({
      type: 'Agreement',
      text: `${successCount} of ${modelResponses.length} council models responded, but synthesis could not run with the available reviews.`,
    })
  } else {
    discussion.push({
      type: 'Agreement',
      text: agg.length ? 'Peer rankings recorded — no consensus first place was parseable.' : 'No consensus ranking was produced.',
    })
  }

  if (stage3 && stage3.success === false) {
    discussion.push({
      type: 'Different view',
      text: stage3.error || 'Chairman synthesis failed.',
    })
  } else if (reviews.length) {
    const reviewerNames = reviews.map((r) => displayName[r.model] || r.model)
    discussion.push({
      type: 'Different view',
      text: `${reviews.length} reviewer${reviews.length > 1 ? 's' : ''} (${reviewerNames.join(', ')}) evaluated ${displayNameAll(modelResponses)} anonymously.`,
    })
    const excerpt = (reviews[0].ranking || '').replace(/\s+/g, ' ').slice(0, 180)
    if (excerpt) discussion.push({ type: 'Additional insight', text: excerpt })
  } else if (Array.isArray(stage2) && stage2.length === 0) {
    discussion.push({ type: 'Different view', text: 'No peer reviews ran — not enough successful responses.' })
  }

  // Single final-answer contract. New payloads/persisted messages carry
  // final_answer explicitly; legacy records fall back to a SUCCESSFUL
  // chairman response only (never a scraped discussion excerpt).
  const finalAnswer = payload.final_answer
    || (stage3 && stage3.success !== false ? stage3.response : null)
    || null
  const synthesisError = payload.final_answer_error
    || (stage3 && stage3.success === false ? (stage3.error || 'Chairman synthesis failed.') : null)

  discussion.push({
    type: 'Synthesis',
    text: finalAnswer
      ? finalAnswer.replace(/\s+/g, ' ').slice(0, 280)
      : (synthesisError || 'Chairman synthesis unavailable.'),
  })

  const criteria = metadata.evaluation?.criteria

  return {
    question: fallbackQuestion,
    model_responses: modelResponses,
    aggregate_rankings: agg,
    peer_reviews: peerReviews,
    label_to_model: labelToModel,
    evaluation_criteria: Array.isArray(criteria) ? criteria : [],
    final_answer: finalAnswer,
    final_answer_error: synthesisError,
    synthesis: finalAnswer || '',
    synthesis_error: synthesisError,
    synthesis_model: stage3 ? (displayName[stage3.model] || stage3.model) : null,
    discussion,
    search: metadata.search || { enabled: false },
    sources,
    failed: false,
    streaming: false,
  }
}

function displayNameAll(modelResponses) {
  const names = modelResponses.filter((m) => m.success).map((m) => m.model)
  if (names.length === 0) return 'no response'
  if (names.length === 1) return names[0]
  return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1]
}
