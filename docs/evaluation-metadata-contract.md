# Evaluation metadata contract

The comparison UI uses data the backend already returns. It does not invent
scores or winners.

## Currently supported (no backend change)

- `metadata.aggregate_rankings[]`
  - `model`: model id
  - `average_rank`: number, lower is better
  - `rankings_count`: number of peer rankings that placed the model
- `metadata.label_to_model`: anonymous label → model id
- `stage2[].parsed_ranking`: ordered anonymous labels from one reviewer
- `stage2[].ranking`: the reviewer's raw evaluation text
- `stage2[].success` / `stage2[].error`: review status, kept separate from
  Stage 1 model response status

A winner is shown only when exactly one model has the lowest
`average_rank`. Ties are reported as ties. If `aggregate_rankings` is empty,
the UI shows per-model response status and states that peer ranking scores are
unavailable.

## Proposed optional contract (requires backend work)

Add per-criterion evaluation only if the backend starts producing it. Keep the
existing fields above for backward compatibility.

```json
{
  "metadata": {
    "evaluation": {
      "criteria": [
        { "id": "relevance", "label": "Relevance", "description": "Answers the question asked" },
        { "id": "completeness", "label": "Completeness" },
        { "id": "factual_consistency", "label": "Factual consistency" },
        { "id": "clarity", "label": "Clarity" }
      ],
      "model_scores": [
        {
          "model_id": "provider/model-id",
          "criterion_id": "relevance",
          "score": 4.25,
          "max_score": 5,
          "sample_size": 3
        }
      ]
    }
  }
}
```

Rules:

- `score` must be a measured value from actual reviewer output. Never emit a
  placeholder or a value derived from response time or token count.
- `sample_size` is the number of reviews contributing to the score.
- Omit a model/criterion pair when no review scored it; the UI shows the model
  as unranked rather than zero.
- Keep Stage 1 model errors in `stage1[].success` / `stage1[].error` and
  Stage 2 review errors in `stage2[].success` / `stage2[].error`.
- `aggregate_rankings` remains the fallback and the only source of the current
  winner rule until the backend ships scored criteria.
