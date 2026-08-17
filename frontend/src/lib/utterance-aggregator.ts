export function concatFloat32(parts: Float32Array[]): Float32Array {
  const total = parts.reduce((sum, p) => sum + p.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const p of parts) {
    out.set(p, offset)
    offset += p.length
  }
  return out
}

/**
 * Collects VAD speech segments and emits them as one utterance after
 * `waitMs` of silence (no new segments). Lets learners pause mid-thought
 * without the tutor answering a fragment.
 */
export class UtteranceAggregator {
  private segments: Float32Array[] = []
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(
    private waitMs: number,
    private onFlush: (audio: Float32Array) => void
  ) {}

  addSegment(segment: Float32Array): void {
    this.segments.push(segment)
    this.restartTimer()
  }

  setWaitMs(ms: number): void {
    this.waitMs = ms
    if (this.timer !== null) this.restartTimer()
  }

  hasPending(): boolean {
    return this.segments.length > 0
  }

  flushNow(): void {
    this.clearTimer()
    if (this.segments.length === 0) return
    const audio = concatFloat32(this.segments)
    this.segments = []
    this.onFlush(audio)
  }

  cancel(): void {
    this.clearTimer()
    this.segments = []
  }

  private restartTimer(): void {
    this.clearTimer()
    this.timer = setTimeout(() => {
      this.timer = null
      this.flushNow()
    }, this.waitMs)
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }
}
