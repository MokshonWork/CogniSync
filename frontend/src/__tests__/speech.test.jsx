import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import SpeechControls from '../SpeechControls.jsx'
import { stop, stripForSpeech } from '../speech.js'

let synth
let utterances

function installSpeechMock(voices = []) {
  utterances = []
  synth = {
    speak: vi.fn((u) => utterances.push(u)),
    cancel: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    getVoices: vi.fn(() => voices),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }
  window.speechSynthesis = synth
  window.SpeechSynthesisUtterance = class {
    constructor(text) {
      this.text = text
      this.voice = null
      this.lang = ''
      this.onend = null
      this.onerror = null
    }
  }
  return synth
}

beforeEach(() => {
  installSpeechMock()
  stop()
  synth.cancel.mockClear()
})

afterEach(() => {
  delete window.speechSynthesis
  delete window.SpeechSynthesisUtterance
})

describe('stripForSpeech', () => {
  it('removes Markdown syntax that should not be read aloud', () => {
    const source = '# Heading\n\n**Bold** and _italic_ with `inline code`.\n\n```js\nconst x = 1\n```\n\n- bullet\n\n[docs](https://example.com)\n\n| a | b |\n| --- | --- |\n| 1 | 2 |'
    const spoken = stripForSpeech(source)
    expect(spoken).not.toMatch(/[#*`]/)
    expect(spoken).not.toContain('https://')
    expect(spoken).not.toContain('](')
    expect(spoken).toContain('Heading')
    expect(spoken).toContain('Bold and italic with inline code')
    expect(spoken).toContain('docs')
  })

  it('returns empty output for empty input', () => {
    expect(stripForSpeech('')).toBe('')
    expect(stripForSpeech(null)).toBe('')
    expect(stripForSpeech('   \n  ')).toBe('')
  })
})

describe('SpeechControls', () => {
  it('reads the displayed answer through the browser speech API', async () => {
    const user = userEvent.setup()
    render(<SpeechControls text={'# Heading\n\n**Bold** answer with `code`.'} />)

    await user.click(screen.getByRole('button', { name: /listen/i }))

    expect(synth.speak).toHaveBeenCalledTimes(1)
    expect(utterances[0].text).toBe('Heading Bold answer with code.')
    expect(screen.getByRole('status')).toHaveTextContent('Speaking')
    expect(screen.getByRole('button', { name: 'Pause speech' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /listen/i })).toBeNull()
  })

  it('pauses, resumes, and stops playback with visible state changes', async () => {
    const user = userEvent.setup()
    render(<SpeechControls text="A real final answer." />)

    await user.click(screen.getByRole('button', { name: /listen/i }))

    await user.click(screen.getByRole('button', { name: 'Pause speech' }))
    expect(synth.pause).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('Paused')

    await user.click(screen.getByRole('button', { name: 'Resume speech' }))
    expect(synth.resume).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Pause speech' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Stop speech' }))
    expect(synth.cancel).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /listen/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause speech' })).toBeNull()
  })

  it('disables listening for an empty answer', async () => {
    render(<SpeechControls text={'   \n '} />)

    const listen = screen.getByRole('button', { name: /listen/i })
    expect(listen).toBeDisabled()
    fireEvent.click(listen)
    expect(synth.speak).not.toHaveBeenCalled()
  })

  it('renders nothing when the browser has no speech API', () => {
    delete window.speechSynthesis
    delete window.SpeechSynthesisUtterance

    const { container } = render(<SpeechControls text="Answer" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('cancels speech when unmounted', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<SpeechControls text="Spoken answer" />)

    await user.click(screen.getByRole('button', { name: /listen/i }))
    expect(synth.speak).toHaveBeenCalledTimes(1)

    unmount()
    expect(synth.cancel).toHaveBeenCalled()
  })

  it('stops old speech when the answer changes', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<SpeechControls text="First answer" />)

    await user.click(screen.getByRole('button', { name: /listen/i }))
    rerender(<SpeechControls text="Second answer" />)

    expect(synth.cancel).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /listen/i })).toBeInTheDocument()
  })

  it('waits for asynchronously loaded voices and offers their languages', async () => {
    const user = userEvent.setup()
    let onVoicesChanged = null
    const voices = [
      { name: 'Alice', lang: 'en-US', default: true },
      { name: 'Amelie', lang: 'fr-FR', default: false },
    ]
    const mock = installSpeechMock([])
    mock.addEventListener = vi.fn((event, fn) => { if (event === 'voiceschanged') onVoicesChanged = fn })
    stop()
    mock.cancel.mockClear()

    render(<SpeechControls text="Hello there" />)
    expect(screen.queryByRole('combobox', { name: 'Speech language' })).toBeNull()

    mock.getVoices = vi.fn(() => voices)
    act(() => onVoicesChanged())

    const select = await screen.findByRole('combobox', { name: 'Speech language' })
    expect(select).toHaveValue('en-US')

    await user.selectOptions(select, 'fr-FR')
    await user.click(screen.getByRole('button', { name: /listen/i }))
    expect(utterances[0].voice.lang).toBe('fr-FR')
    expect(utterances[0].lang).toBe('fr-FR')
  })
})
