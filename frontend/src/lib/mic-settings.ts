export type MicMode = 'auto' | 'manual'

export interface MicSettings {
  mode: MicMode
  /** Seconds of silence before the utterance is sent. null = default (CEFR-based VAD, immediate send). */
  waitSeconds: number | null
}

export const MIC_WAIT_MIN = 2
export const MIC_WAIT_MAX = 20

const MODE_KEY = 'fl_mic_mode'
const WAIT_KEY = 'fl_mic_wait_s'

const DEFAULT_MIC_SETTINGS: MicSettings = { mode: 'auto', waitSeconds: null }

export function loadMicSettings(): MicSettings {
  if (typeof window === 'undefined') {
    return { ...DEFAULT_MIC_SETTINGS }
  }
  try {
    const rawMode = localStorage.getItem(MODE_KEY)
    const mode: MicMode = rawMode === 'manual' ? 'manual' : 'auto'
    const rawWait = localStorage.getItem(WAIT_KEY)
    let waitSeconds: number | null = null
    if (rawWait !== null) {
      const parsed = Number.parseInt(rawWait, 10)
      if (Number.isFinite(parsed)) {
        waitSeconds = Math.min(MIC_WAIT_MAX, Math.max(MIC_WAIT_MIN, parsed))
      }
    }
    return { mode, waitSeconds }
  } catch {
    // Storage access can throw (blocked cookies, enterprise policy, etc).
    // Fall back to defaults rather than white-screening the page.
    return { ...DEFAULT_MIC_SETTINGS }
  }
}

export function saveMicSettings(s: MicSettings): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(MODE_KEY, s.mode)
    if (s.waitSeconds === null) {
      localStorage.removeItem(WAIT_KEY)
    } else {
      localStorage.setItem(WAIT_KEY, String(s.waitSeconds))
    }
  } catch {
    // Storage write can throw (quota exceeded, blocked storage). Non-fatal:
    // the setting simply won't persist across reloads.
  }
}
