import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'

import LibraryView from '../views/LibraryView.jsx'

const PROMPTS = [
  'Compare the main approaches to solve a problem, listing strengths, weaknesses, trade-offs, assumptions, and the situations in which each approach is the better choice. End with a concise comparison table and a recommendation based on the stated priorities.',
  'I need to decide between [option A] and [option B]. Compare them against my goals, constraints, cost, implementation effort, risks, reversibility, and long-term consequences. Identify the information that could change the decision, then recommend one option with a clear rationale. If critical context is missing, state your assumptions.',
  'Here is my argument: [argument]. Identify its explicit and hidden assumptions, examine the evidence supporting each major claim, find logical gaps and counterexamples, and present the strongest opposing argument. Distinguish facts from speculation and suggest how the argument could be improved.',
  'Rewrite the following text to make it clearer, more concise, and better structured without changing its intended meaning: [text]. Preserve important details, use natural language, remove unnecessary repetition, and briefly explain any substantial changes.',
  'Review the following code for correctness, security, edge cases, performance, maintainability, and simpler alternatives: [code]. Prioritize findings by severity, explain why each issue matters, and suggest concrete fixes. Do not invent bugs; distinguish confirmed issues from potential risks. If the code is sound, say so and recommend useful tests.',
]

describe('LibraryView built-in prompts', () => {
  it('uses the exact requested prompt texts and placeholders', () => {
    render(<LibraryView onUsePrompt={vi.fn()} />)
    for (const text of PROMPTS) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
    expect(screen.getByText(PROMPTS[1])).toHaveTextContent('[option A]')
    expect(screen.getByText(PROMPTS[4])).toHaveTextContent('[code]')
  })

  it('passes the full prompt to the composer without submitting', async () => {
    const user = userEvent.setup()
    const onUsePrompt = vi.fn()
    render(<LibraryView onUsePrompt={onUsePrompt} />)

    await user.click(screen.getAllByRole('button', { name: 'Use prompt' })[0])

    expect(onUsePrompt).toHaveBeenCalledWith(PROMPTS[0])
  })
})
