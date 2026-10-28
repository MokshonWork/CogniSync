const listeners = new Set()
let state = { status: 'idle', error: null }
let owner = null

function update(patch) {
  state = { ...state, ...patch }
  for (const fn of listeners) fn(state)
}

export function isSpeechSupported() {
  return typeof window !== 'undefined'
    && typeof window.speechSynthesis !== 'undefined'
    && typeof window.SpeechSynthesisUtterance !== 'undefined'
}

export function subscribe(fn) {
  listeners.add(fn)
  fn(state)
  return () => listeners.delete(fn)
}

export function getSpeechState() {
  return state
}

const ERROR_MESSAGES = {
  'not-allowed': 'Speech was blocked. Allow audio for this site, then try again.',
  'no-speech': 'No speech was detected.',
  'audio-busy': 'The audio device is busy.',
  'audio-hardware': 'No audio output device was found.',
  network: 'The speech service is unavailable (network error).',
  'synthesis-unavailable': 'Speech synthesis is unavailable in this browser.',
  'synthesis-failed': 'Speech synthesis failed.',
  'language-unavailable': 'This language is unavailable for speech.',
  'voice-unavailable': 'This voice is unavailable for speech.',
}

export function speak(text, { voice = null, ownerId = null } = {}) {
  if (!isSpeechSupported()) return false
  const spoken = stripForSpeech(text)
  if (!spoken) return false
  const synth = window.speechSynthesis
  synth.cancel()
  const utterance = new window.SpeechSynthesisUtterance(spoken)
  if (voice) {
    utterance.voice = voice
    if (voice.lang) utterance.lang = voice.lang
  }
  utterance.onend = () => {
    if (owner === ownerId) {
      owner = null
      update({ status: 'idle' })
    }
  }
  utterance.onerror = (event) => {
    if (owner !== ownerId) return
    owner = null
    const code = event?.error || ''
    if (code === 'interrupted' || code === 'canceled') {
      update({ status: 'idle' })
    } else {
      update({ status: 'idle', error: ERROR_MESSAGES[code] || 'Speech could not be played.' })
    }
  }
  owner = ownerId
  update({ status: 'speaking', error: null })
  synth.speak(utterance)
  return true
}

export function pause() {
  if (!isSpeechSupported() || state.status !== 'speaking') return
  window.speechSynthesis.pause()
  update({ status: 'paused' })
}

export function resume() {
  if (!isSpeechSupported() || state.status !== 'paused') return
  window.speechSynthesis.resume()
  update({ status: 'speaking' })
}

// Pass an ownerId only for cleanup on unmount: it must not cancel speech that
// another instance (e.g. the full-answer overlay) started.
export function stop(ownerId = null) {
  if (!isSpeechSupported()) return
  if (ownerId && owner && owner !== ownerId) return
  window.speechSynthesis.cancel()
  owner = null
  update({ status: 'idle' })
}

export function pickVoice(voices, lang) {
  if (!voices.length) return null
  const matching = voices.filter((v) => v.lang === lang)
  return matching.find((v) => v.default) || matching[0] || voices.find((v) => v.lang?.startsWith(lang)) || voices[0]
}

// Strip Markdown syntax that speech engines read aloud as noise.
export function stripForSpeech(markdown) {
  return String(markdown ?? '')
    .replace(/```[\w-]*\n?/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*_])(?:\s*\1){2,}\s*$/gm, '')
    .replace(/^\s*\|?[\s:|-]*\|[\s:|-]*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1$2')
    .replace(/(^|[\s(])_([^_\n]+)_/g, '$1$2')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}
