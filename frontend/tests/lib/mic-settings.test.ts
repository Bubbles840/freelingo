import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  loadMicSettings,
  saveMicSettings,
  MIC_WAIT_MIN,
  MIC_WAIT_MAX,
} from '@/lib/mic-settings'

describe('mic-settings', () => {
  beforeEach(() => localStorage.clear())

  it('defaults to auto mode with null wait', () => {
    expect(loadMicSettings()).toEqual({ mode: 'auto', waitSeconds: null })
  })

  it('round-trips saved settings', () => {
    saveMicSettings({ mode: 'manual', waitSeconds: 12 })
    expect(loadMicSettings()).toEqual({ mode: 'manual', waitSeconds: 12 })
  })

  it('treats a null wait as default and removes the key', () => {
    saveMicSettings({ mode: 'auto', waitSeconds: 12 })
    saveMicSettings({ mode: 'auto', waitSeconds: null })
    expect(localStorage.getItem('fl_mic_wait_s')).toBeNull()
    expect(loadMicSettings().waitSeconds).toBeNull()
  })

  it('clamps out-of-range and garbage stored values', () => {
    localStorage.setItem('fl_mic_wait_s', '99')
    expect(loadMicSettings().waitSeconds).toBe(MIC_WAIT_MAX)
    localStorage.setItem('fl_mic_wait_s', '0')
    expect(loadMicSettings().waitSeconds).toBe(MIC_WAIT_MIN)
    localStorage.setItem('fl_mic_wait_s', 'bananas')
    expect(loadMicSettings().waitSeconds).toBeNull()
    localStorage.setItem('fl_mic_mode', 'bananas')
    expect(loadMicSettings().mode).toBe('auto')
  })

  it('falls back to defaults instead of throwing when getItem throws', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('storage access blocked')
      })
    try {
      expect(() => loadMicSettings()).not.toThrow()
      expect(loadMicSettings()).toEqual({ mode: 'auto', waitSeconds: null })
    } finally {
      spy.mockRestore()
    }
  })

  it('silently ignores a setItem failure instead of throwing', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota exceeded')
      })
    try {
      expect(() =>
        saveMicSettings({ mode: 'manual', waitSeconds: 10 })
      ).not.toThrow()
    } finally {
      spy.mockRestore()
    }
  })
})
