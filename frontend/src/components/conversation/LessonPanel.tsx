'use client'

import { useEffect, useRef } from 'react'
import { useTranslations } from 'next-intl'

export interface LessonPanelStep {
  title: string
  status: 'done' | 'current' | 'pending'
  kind?: string
  prompt?: string
  options?: string[]
}

export default function LessonPanel({
  title,
  steps,
  stepIndex,
  total,
  visible,
  onToggle,
  onAdvance,
}: {
  title: string
  steps: LessonPanelStep[]
  stepIndex: number
  total: number
  visible: boolean
  onToggle: () => void
  onAdvance: () => void
}) {
  const t = useTranslations('conversation')
  const currentStepRef = useRef<HTMLLIElement | null>(null)

  // step_index is the number of *completed* steps, so once the lesson
  // finishes it equals total — clamp the displayed "current" number so we
  // never render e.g. "step 6 of 5".
  const currentDisplay = Math.min(stepIndex + 1, total)

  useEffect(() => {
    currentStepRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [stepIndex])

  return (
    <div className="border-fl-border bg-fl-surface flex h-full min-h-0 flex-col border">
      <div className="border-fl-border flex items-center justify-between border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-fl-label text-fl-muted-2">●</span>
          <span className="text-fl-fg truncate font-mono text-sm font-bold tracking-wide">
            {title}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-fl-hint text-fl-muted-2 font-mono tracking-widest uppercase">
            {t('lessonStepProgress', { current: currentDisplay, total })}
          </span>
          <button
            type="button"
            onClick={onToggle}
            className="text-fl-hint text-fl-muted-3 hover:text-fl-fg font-mono tracking-widest uppercase transition-colors"
          >
            {visible ? t('lessonHide') : t('lessonShow')}
          </button>
        </div>
      </div>

      {visible && (
        <ul className="max-h-40 min-h-0 flex-1 space-y-1 overflow-y-auto px-4 py-3 lg:max-h-none">
          {steps.map((step, i) => {
            const isCurrent = step.status === 'current'
            const isDone = step.status === 'done'
            return (
              <li
                key={i}
                ref={isCurrent ? currentStepRef : undefined}
                aria-current={isCurrent ? 'step' : undefined}
                data-testid={`lesson-step-${step.status}`}
                className={`flex items-center gap-2 border-l-2 px-2 py-1 font-mono text-xs transition-colors ${
                  isCurrent
                    ? 'border-fl-accent text-fl-fg'
                    : 'border-fl-border text-fl-muted-1'
                }`}
              >
                {isDone && (
                  <span aria-hidden="true" className="text-green-400">
                    ✓
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <span className={isDone ? 'text-fl-muted-3' : ''}>
                    {step.title}
                  </span>
                  {/* Fork: the current exercise shows its real question and
                      choices — the learner answers by voice. */}
                  {isCurrent && step.prompt && (
                    <p
                      data-testid="lesson-exercise-prompt"
                      className="text-fl-fg mt-1.5 font-mono text-xs leading-relaxed"
                    >
                      {step.prompt}
                    </p>
                  )}
                  {isCurrent && step.options && step.options.length > 0 && (
                    <div
                      data-testid="lesson-exercise-options"
                      className="mt-1.5 flex flex-wrap gap-1.5"
                    >
                      {step.options.map((option) => (
                        <span
                          key={option}
                          className="border-fl-border bg-fl-surface-2 text-fl-muted-1 border px-2 py-1 font-mono text-xs"
                        >
                          {option}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      <div className="border-fl-border border-t px-4 py-3">
        <button
          type="button"
          onClick={onAdvance}
          className="border-fl-border text-fl-muted-1 hover:text-fl-fg hover:border-fl-border-2 w-full border px-4 py-2 font-mono text-xs tracking-widest uppercase transition-colors"
        >
          {t('lessonNextSection')}
        </button>
      </div>
    </div>
  )
}
