import Image from 'next/image'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { TargetLanguageText } from '@/components/TargetLanguageText'
import { AuthAvatarImage } from '@/components/AuthAvatarImage'
import type { PronunciationWordScore } from '@/lib/conversation-ws'

const WEAK_WORD_SCORE_THRESHOLD = 60

export interface PronunciationInfo {
  overall: number
  fluency: number | null
  words: PronunciationWordScore[]
}

function scoreToneClass(score: number): string {
  if (score >= 80) return 'text-fl-success'
  if (score >= WEAK_WORD_SCORE_THRESHOLD) return 'text-fl-muted-2'
  return 'text-fl-error'
}

// Underline colors tuned to read on the gold user-bubble background —
// the page-level success/error tokens are too bright against it.
function wordDecorationClass(score: number): string {
  if (score >= 80) return 'underline decoration-green-800 decoration-2 underline-offset-4'
  if (score >= WEAK_WORD_SCORE_THRESHOLD) return ''
  return 'underline decoration-red-800 decoration-wavy decoration-2 underline-offset-4'
}

function normalizeWord(word: string): string {
  return word.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '')
}

/**
 * Fork: paint pronunciation scores onto the transcript words themselves.
 * Scripted assessment scores the STT transcript, so the two word sequences
 * align by construction; the small lookahead absorbs tokenisation quirks
 * (e.g. "¿Y" vs "y"). Returns null when alignment fails, so callers can
 * fall back to plain text instead of mis-colouring.
 */
function alignScores(
  text: string,
  words: { word: string; score: number }[]
): { token: string; score: number | null }[] | null {
  const tokens = text.split(/(\s+)/)
  let wi = 0
  let matched = 0
  const result = tokens.map((token) => {
    const norm = normalizeWord(token)
    if (!norm) return { token, score: null }
    for (let look = 0; look < 2 && wi + look < words.length; look++) {
      if (normalizeWord(words[wi + look].word) === norm) {
        const score = words[wi + look].score
        wi += look + 1
        matched++
        return { token, score }
      }
    }
    return { token, score: null }
  })
  const scorable = tokens.filter((t) => normalizeWord(t)).length
  if (scorable === 0 || matched / scorable < 0.6) return null
  return result
}

interface Props {
  role: 'user' | 'assistant'
  text: string
  /** Fork: fires with the bubble's full text when the user selects part of it */
  onTextSelected?: (text: string) => void
  streaming?: boolean
  speaking?: boolean
  userAvatar?: string | null
  userInitial?: string
  languageCode?: string | null
  pronunciation?: PronunciationInfo
}

export default function TranscriptBubble({
  role,
  text,
  onTextSelected,
  streaming = false,
  speaking = false,
  userAvatar,
  userInitial,
  languageCode,
  pronunciation,
}: Props) {
  const t = useTranslations('conversation')
  const isUser = role === 'user'

  // Fork: tap-to-translate — both sides of the conversation, into the
  // learner's native language, fetched once and toggled locally.
  const [translation, setTranslation] = useState<string | null>(null)
  const [showTranslation, setShowTranslation] = useState(false)
  const [translating, setTranslating] = useState(false)
  const [translateError, setTranslateError] = useState(false)

  async function toggleTranslation() {
    if (showTranslation) {
      setShowTranslation(false)
      return
    }
    setTranslateError(false)
    if (translation !== null) {
      setShowTranslation(true)
      return
    }
    setTranslating(true)
    try {
      const res = await apiFetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (!res.ok) throw new Error('translation_failed')
      const data = (await res.json()) as { translation: string }
      setTranslation(data.translation)
      setShowTranslation(true)
    } catch {
      setTranslateError(true)
    } finally {
      setTranslating(false)
    }
  }

  return (
    <div
      className={`flex items-end gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
    >
      {/* Avatar */}
      <div className="relative mb-0.5 flex-shrink-0">
        <span
          className={`pointer-events-none absolute inset-[-5px] rounded-full border-2 transition-[border-color,opacity] duration-700 ${
            speaking
              ? 'border-fl-accent/65 animate-halo-speaking'
              : 'border-fl-accent/15 animate-halo-idle'
          }`}
        />
        <div className="border-fl-border h-7 w-7 overflow-hidden rounded-full border">
          {!isUser ? (
            <Image
              src="/logo_head.png"
              alt="Tutor"
              width={28}
              height={28}
              className="h-full w-full object-cover"
            />
          ) : userAvatar ? (
            <AuthAvatarImage
              avatar={userAvatar}
              alt=""
              width={28}
              height={28}
              className="h-full w-full object-cover"
              fallback={
                <div className="bg-fl-surface-2 flex h-full w-full items-center justify-center">
                  <span className="text-fl-hint text-fl-muted-1 font-mono select-none">
                    {(userInitial ?? '?').toUpperCase()}
                  </span>
                </div>
              }
            />
          ) : (
            <div className="bg-fl-surface-2 flex h-full w-full items-center justify-center">
              <span className="text-fl-hint text-fl-muted-1 font-mono select-none">
                {(userInitial ?? '?').toUpperCase()}
              </span>
            </div>
          )}
        </div>
      </div>

      <div
        className={`flex max-w-[75%] flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}
      >
        <span className="text-fl-label text-fl-muted-4 font-mono tracking-widest uppercase">
          {isUser ? t('you') : t('assistant')}
        </span>
        <TargetLanguageText
          as="div"
          languageCode={languageCode}
          onPointerUp={onTextSelected ? () => onTextSelected(text) : undefined}
          className={`word-selectable cursor-text select-text border px-4 py-3 ${
            isUser
              ? 'bg-fl-accent text-fl-accent-fg border-fl-accent'
              : 'bg-fl-surface text-fl-fg border-fl-border'
          }`}
        >
          {(() => {
            if (!isUser || !pronunciation || streaming) return text
            const aligned = alignScores(text, pronunciation.words)
            if (!aligned) return text
            return aligned.map(({ token, score }, i) =>
              score === null ? (
                token
              ) : (
                <span key={i} className={wordDecorationClass(score)}>
                  {token}
                </span>
              )
            )
          })()}
          {streaming && (
            <span className="ml-1 inline-block h-3 w-1 animate-pulse bg-current align-middle" />
          )}
        </TargetLanguageText>
        {!streaming && text.trim() !== '' && (
          <div className={`flex flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
            <button
              type="button"
              onClick={() => void toggleTranslation()}
              disabled={translating}
              className="text-fl-hint text-fl-muted-3 hover:text-fl-fg font-mono tracking-widest uppercase transition-colors disabled:opacity-50"
            >
              {translating
                ? '…'
                : showTranslation
                  ? t('hideTranslation')
                  : t('translate')}
            </button>
            {translateError && (
              <span className="text-fl-hint text-fl-error font-mono">
                {t('translationFailed')}
              </span>
            )}
            {showTranslation && translation && (
              <p className="border-fl-border bg-fl-surface-2 text-fl-muted-1 max-w-full border px-3 py-2 font-mono text-xs leading-relaxed">
                {translation}
              </p>
            )}
          </div>
        )}
        {isUser && pronunciation && (
          <div className="flex flex-col items-end gap-0.5">
            <span
              data-testid="pronunciation-score"
              className={`text-fl-label font-mono tracking-widest uppercase ${scoreToneClass(pronunciation.overall)}`}
            >
              {t('pronunciationScore', { score: pronunciation.overall })}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
