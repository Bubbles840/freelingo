'use client'

import { useTranslations } from 'next-intl'
import {
  MIC_WAIT_MAX,
  MIC_WAIT_MIN,
  type MicSettings,
} from '@/lib/mic-settings'

export default function MicModeControl({
  settings,
  onChange,
}: {
  settings: MicSettings
  onChange: (next: MicSettings) => void
}) {
  const t = useTranslations('conversation')
  const sliderValue = settings.waitSeconds ?? MIC_WAIT_MIN - 1

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="border-fl-border inline-flex rounded-full border p-0.5"
        role="group"
        aria-label={t('micModeLabel')}
      >
        {(['auto', 'manual'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => onChange({ ...settings, mode })}
            className={`rounded-full px-3 py-1 font-mono text-xs tracking-widest uppercase transition-colors ${
              settings.mode === mode
                ? 'bg-fl-accent text-fl-accent-fg'
                : 'text-fl-muted-2 hover:text-fl-fg'
            }`}
          >
            {mode === 'auto' ? t('micAuto') : t('micManual')}
          </button>
        ))}
      </div>
      {settings.mode === 'auto' && (
        <label className="text-fl-muted-2 flex items-center gap-2 font-mono text-xs">
          <span className="tracking-widest uppercase">
            {t('micWaitLabel')}
          </span>
          <input
            type="range"
            min={MIC_WAIT_MIN - 1}
            max={MIC_WAIT_MAX}
            step={1}
            value={sliderValue}
            onChange={(e) => {
              const v = Number(e.target.value)
              onChange({
                ...settings,
                waitSeconds: v < MIC_WAIT_MIN ? null : v,
              })
            }}
            className="accent-fl-accent h-1.5 w-28 cursor-pointer"
          />
          <span className="w-14 text-right tabular-nums">
            {settings.waitSeconds === null
              ? t('micWaitDefault')
              : t('micWaitSeconds', { seconds: settings.waitSeconds })}
          </span>
        </label>
      )}
    </div>
  )
}
