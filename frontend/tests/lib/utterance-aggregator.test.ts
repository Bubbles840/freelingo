import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  UtteranceAggregator,
  concatFloat32,
} from '@/lib/utterance-aggregator'

const seg = (...values: number[]) => new Float32Array(values)

describe('concatFloat32', () => {
  it('concatenates in order', () => {
    const out = concatFloat32([seg(1, 2), seg(3), seg(4, 5)])
    expect(Array.from(out)).toEqual([1, 2, 3, 4, 5])
  })

  it('handles empty input', () => {
    expect(concatFloat32([]).length).toBe(0)
  })
})

describe('UtteranceAggregator', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('flushes concatenated segments after waitMs of no new segments', () => {
    const flushed: Float32Array[] = []
    const agg = new UtteranceAggregator(5000, (a) => flushed.push(a))
    agg.addSegment(seg(1, 2))
    vi.advanceTimersByTime(4000)
    agg.addSegment(seg(3))          // resets the timer
    vi.advanceTimersByTime(4999)
    expect(flushed).toHaveLength(0) // not yet
    vi.advanceTimersByTime(1)
    expect(flushed).toHaveLength(1)
    expect(Array.from(flushed[0])).toEqual([1, 2, 3])
    expect(agg.hasPending()).toBe(false)
  })

  it('setWaitMs reschedules a pending flush', () => {
    const flushed: Float32Array[] = []
    const agg = new UtteranceAggregator(20000, (a) => flushed.push(a))
    agg.addSegment(seg(1))
    agg.setWaitMs(2000)
    vi.advanceTimersByTime(2000)
    expect(flushed).toHaveLength(1)
  })

  it('cancel drops pending audio', () => {
    const flushed: Float32Array[] = []
    const agg = new UtteranceAggregator(1000, (a) => flushed.push(a))
    agg.addSegment(seg(1))
    agg.cancel()
    vi.advanceTimersByTime(5000)
    expect(flushed).toHaveLength(0)
    expect(agg.hasPending()).toBe(false)
  })

  it('flushNow fires immediately only when pending', () => {
    const flushed: Float32Array[] = []
    const agg = new UtteranceAggregator(1000, (a) => flushed.push(a))
    agg.flushNow()
    expect(flushed).toHaveLength(0)
    agg.addSegment(seg(7))
    agg.flushNow()
    expect(flushed).toHaveLength(1)
    vi.advanceTimersByTime(5000)
    expect(flushed).toHaveLength(1) // timer was cleared by flushNow
  })
})
