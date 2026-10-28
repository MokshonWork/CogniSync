import { useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from './icons.jsx'
import {
  getSpeechState,
  isSpeechSupported,
  pause,
  pickVoice,
  resume,
  speak,
  stop,
  stripForSpeech,
  subscribe,
} from './speech.js'

let instanceCount = 0

function languageLabel(tag) {
  try {
    const locale = typeof navigator !== 'undefined' && navigator.language ? navigator.language : 'en'
    const name = new Intl.DisplayNames([locale], { type: 'language' }).of(tag)
    return name ? `${name} (${tag})` : tag
  } catch {
    return tag
  }
}

export default function SpeechControls({ text, className = '' }) {
  const supported = isSpeechSupported()
  const [voices, setVoices] = useState([])
  const [lang, setLang] = useState('')
  const [speech, setSpeech] = useState(getSpeechState)
  const idRef = useRef(null)
  if (idRef.current === null) idRef.current = `speech-${++instanceCount}`
  const textRef = useRef(text)

  useEffect(() => subscribe(setSpeech), [])

  useEffect(() => {
    if (!supported) return
    const synth = window.speechSynthesis
    const load = () => {
      const next = synth.getVoices?.() || []
      if (next.length) setVoices(next)
    }
    load()
    if (synth.addEventListener) synth.addEventListener('voiceschanged', load)
    else synth.onvoiceschanged = load
    return () => {
      if (synth.removeEventListener) synth.removeEventListener('voiceschanged', load)
      else if (synth.onvoiceschanged === load) synth.onvoiceschanged = null
    }
  }, [supported])

  const languages = useMemo(() => {
    const seen = new Set()
    const out = []
    for (const voice of voices) {
      if (voice.lang && !seen.has(voice.lang)) {
        seen.add(voice.lang)
        out.push(voice.lang)
      }
    }
    return out
  }, [voices])

  useEffect(() => {
    if (!languages.length) return
    if (lang && languages.includes(lang)) return
    setLang(languages.find((l) => /^en(-|$)/i.test(l)) || languages[0])
  }, [languages, lang])

  // A new answer (another conversation or run) invalidates the old speech.
  useEffect(() => {
    if (textRef.current === text) return
    textRef.current = text
    stop()
  }, [text])

  // Only cancel our own playback on unmount, never a sibling instance's.
  useEffect(() => () => stop(idRef.current), [])

  if (!supported) return null

  const speechText = stripForSpeech(text)
  const empty = !speechText
  const isSpeaking = speech.status === 'speaking'
  const isPaused = speech.status === 'paused'
  const active = isSpeaking || isPaused
  const canChooseLanguage = languages.length > 0

  const listen = () => speak(text, { voice: pickVoice(voices, lang), ownerId: idRef.current })

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {!active && (
        <button
          type="button"
          onClick={listen}
          disabled={empty}
          aria-label="Listen to final answer"
          title={empty ? 'No answer to read' : 'Read the final answer aloud (browser speech)'}
          className="inline-flex items-center gap-1.5 rounded-lg bg-ink px-3 py-2 text-[12px] font-medium text-white hover:bg-black disabled:opacity-30"
        >
          <Icon name="volume" size={13} /> Listen
        </button>
      )}

      {active && (
        <>
          {isSpeaking ? (
            <button
              type="button"
              onClick={pause}
              aria-label="Pause speech"
              className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-neutral-600 hover:border-black/30"
            >
              <Icon name="pause" size={13} /> Pause
            </button>
          ) : (
            <button
              type="button"
              onClick={resume}
              aria-label="Resume speech"
              className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-neutral-600 hover:border-black/30"
            >
              <Icon name="play" size={13} /> Resume
            </button>
          )}
          <button
            type="button"
            onClick={() => stop()}
            aria-label="Stop speech"
            className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-neutral-600 hover:border-black/30"
          >
            <Icon name="stop" size={13} /> Stop
          </button>
        </>
      )}

      {canChooseLanguage && (
        <select
          aria-label="Speech language"
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          disabled={active}
          title={active ? 'Stop playback to change the language' : 'Choose a language for speech playback'}
          className="max-w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-neutral-600 disabled:opacity-50"
        >
          {languages.map((tag) => (
            <option key={tag} value={tag}>{languageLabel(tag)}</option>
          ))}
        </select>
      )}

      {active && (
        <span role="status" aria-live="polite" className="inline-flex items-center gap-1.5 text-[11px] text-neutral-500">
          <span className={`h-1.5 w-1.5 rounded-full ${isSpeaking ? 'animate-pulse bg-gpt' : 'bg-accent'}`} />
          {isSpeaking ? 'Speaking…' : 'Paused'}
        </span>
      )}

      {speech.error && (
        <p role="alert" className="basis-full text-[11px] leading-snug text-red-600">{speech.error}</p>
      )}
    </div>
  )
}
