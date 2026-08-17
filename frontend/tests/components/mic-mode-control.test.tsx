import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import MicModeControl from '@/components/conversation/MicModeControl'
import type { MicSettings } from '@/lib/mic-settings'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

const renderControl = (
  settings: MicSettings = { mode: 'auto', waitSeconds: null },
  onChange = vi.fn()
) => {
  render(<MicModeControl settings={settings} onChange={onChange} />)
  return onChange
}

describe('MicModeControl', () => {
  it('switches to manual mode', () => {
    const onChange = renderControl()
    fireEvent.click(screen.getByRole('button', { name: 'micManual' }))
    expect(onChange).toHaveBeenCalledWith({ mode: 'manual', waitSeconds: null })
  })

  it('shows the wait slider only in auto mode', () => {
    renderControl({ mode: 'auto', waitSeconds: 8 })
    expect(screen.getByRole('slider')).toBeTruthy()
  })

  it('hides the wait slider in manual mode', () => {
    renderControl({ mode: 'manual', waitSeconds: null })
    expect(screen.queryByRole('slider')).toBeNull()
  })

  it('reports slider changes as waitSeconds', () => {
    const onChange = renderControl({ mode: 'auto', waitSeconds: 8 })
    fireEvent.change(screen.getByRole('slider'), { target: { value: '15' } })
    expect(onChange).toHaveBeenCalledWith({ mode: 'auto', waitSeconds: 15 })
  })

  it('slider minimum maps to default (null)', () => {
    const onChange = renderControl({ mode: 'auto', waitSeconds: 8 })
    fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } })
    expect(onChange).toHaveBeenCalledWith({ mode: 'auto', waitSeconds: null })
  })
})
