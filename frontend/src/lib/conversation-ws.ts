/**
 * Builds the WebSocket URL for the /ws/conversation endpoint.
 *
 * The backend WebSocket route is NOT covered by Next.js rewrites (which only
 * handle HTTP). The browser connects directly to the backend using
 * NEXT_PUBLIC_API_URL.
 *
 * Rules:
 *  - If NEXT_PUBLIC_API_URL is set (e.g. "https://api.example.com"), replace
 *    the http(s) scheme with ws(s) and append the path.
 *  - If NEXT_PUBLIC_API_URL is empty (same-origin / Traefik fronting both),
 *    derive the WS base from window.location.
 */
export function buildConversationWsUrl(): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? '')
    .trim()
    .replace(/\/+$/, '')

  let wsBase: string
  if (base) {
    // e.g. "https://api.example.com" → "wss://api.example.com"
    wsBase = base.replace(/^http/, 'ws')
  } else {
    // Same origin — derive from window.location
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    wsBase = `${proto}//${window.location.host}`
  }

  return `${wsBase}/ws/conversation`
}

// ─── Incoming WS message types ───────────────────────────────────────────────

export interface TranscriptMessage {
  type: 'transcript'
  role: 'user' | 'assistant'
  text: string
  final: boolean
  turn_id?: number
}

export interface BargeInMessage {
  type: 'barge_in'
  turn_id?: number
}

export interface SessionWarningMessage {
  type: 'session_warning'
  remaining_seconds: number
  turn_id?: number
}

export interface SessionEndMessage {
  type: 'session_end'
  reason: 'max_duration' | 'inactivity'
  turn_id?: number
}

export interface StatusMessage {
  type: 'status'
  value: 'transcribing' | 'thinking' | 'speaking' | 'listening'
  turn_id?: number
}

export interface ErrorMessage {
  type: 'error'
  code: string
  message?: string
  turn_id?: number
}

export interface MemoryUpdatedMessage {
  type: 'memory_updated'
  turn_id?: number
}

export interface TurnCompleteMessage {
  type: 'turn_complete'
  turn_id?: number
}

export interface PronunciationWordScore {
  word: string
  score: number
}

export interface PronunciationMessage {
  type: 'pronunciation'
  overall: number
  fluency: number | null
  words: PronunciationWordScore[]
  turn_id?: number
}

export interface LessonStateStep {
  title: string
  status: 'done' | 'current' | 'pending'
  /** Fork: step kind from the server plan (intro | key_point | vocabulary |
   * exercise | wrap_up). Absent on older payloads. */
  kind?: string
  /** Fork: full question text — present only on the current exercise step. */
  prompt?: string
  /** Fork: answer choices — present only on the current exercise step. */
  options?: string[]
}

export interface LessonStateMessage {
  type: 'lesson_state'
  lesson_id: number
  step_index: number
  total: number
  steps: LessonStateStep[]
  turn_id?: number
  /** Optional: absent on older payloads — callers should fall back to a
   * generic label when it isn't present. */
  title?: string
}

export interface LessonCompletedMessage {
  type: 'lesson_completed'
  lesson_id: number
  turn_id?: number
}

export type WsMessage =
  | TranscriptMessage
  | BargeInMessage
  | SessionWarningMessage
  | SessionEndMessage
  | StatusMessage
  | ErrorMessage
  | MemoryUpdatedMessage
  | TurnCompleteMessage
  | PronunciationMessage
  | LessonStateMessage
  | LessonCompletedMessage

// ─── Chat context passed from tutor chat to voice session ────────────────────

export interface ChatContextItem {
  role: 'user' | 'assistant'
  content: string
}
