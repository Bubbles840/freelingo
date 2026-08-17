'use client'

import { type ReactNode } from 'react'
import { useTranslations } from 'next-intl'

interface UnitStatus {
  completed: boolean
  active: boolean
  locked: boolean
  isLevelTest: boolean
}

interface Props {
  title: string
  index: number
  lessonCount: number
  grammarCount: number
  competency: number // 0–1, completion ratio
  status: UnitStatus
  onClick: () => void
  /** When provided and status.active, an EMPEZAR CTA is rendered on the card */
  onStartLesson?: () => void
  /** Fork: launch today's lesson as a guided voice lesson (shown beside START) */
  onPracticeLesson?: () => void
  /** Fork: practise today's lesson in a roleplay scenario (shown beside START) */
  onRoleplayLesson?: () => void
}

function StatusIcon({ status }: { status: UnitStatus }): ReactNode {
  if (status.isLevelTest) return <span className="text-fl-muted-1">⊞</span>
  if (status.completed) return <span className="text-fl-fg">✓</span>
  if (status.active) return <span className="text-fl-fg animate-pulse">●</span>
  if (status.locked) return <span className="text-fl-muted-3">○</span>
  return <span className="text-fl-muted-2">◦</span>
}

export default function UnitCard({
  title,
  index,
  lessonCount,
  grammarCount,
  competency,
  status,
  onClick,
  onStartLesson,
  onPracticeLesson,
  onRoleplayLesson,
}: Props) {
  const t = useTranslations('plan')
  const tCommon = useTranslations('common')
  const barWidth = Math.round(competency * 100)

  return (
    <div
      className={`w-full border transition-colors ${
        status.locked
          ? 'border-fl-border opacity-40'
          : status.active
            ? 'border-fl-fg bg-fl-surface'
            : 'border-fl-border bg-fl-surface'
      }`}
    >
      {/* Clickable card header — opens drawer */}
      <button
        onClick={onClick}
        disabled={status.locked}
        className={`group w-full text-left ${
          status.locked
            ? 'cursor-default'
            : status.active
              ? 'hover:bg-fl-surface-2'
              : 'hover:border-fl-border-2'
        }`}
        aria-label={t('unitAriaLabel', { index: index + 1, title })}
      >
        {/* Top bar: status + index + title */}
        <div className="flex items-center gap-3 px-4 py-3">
          <span className="w-4 shrink-0 font-mono text-base">
            <StatusIcon status={status} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-fl-hint text-fl-muted-3 shrink-0 font-mono tracking-widest uppercase">
                {String(index + 1).padStart(2, '0')}
              </span>
              <span
                className={`text-fl-label truncate font-mono ${
                  status.locked
                    ? 'text-fl-muted-3'
                    : 'text-fl-muted-1 group-hover:text-fl-fg'
                }`}
              >
                {title}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-3">
              <span className="text-fl-hint text-fl-muted-3 font-mono">
                {t('nLessons', { count: lessonCount })}
              </span>
              {grammarCount > 0 && (
                <span className="text-fl-hint text-fl-muted-3 font-mono">
                  {t('nGrammar', { count: grammarCount })}
                </span>
              )}
              {status.isLevelTest && (
                <span className="text-fl-hint text-fl-muted-2 border-fl-border border px-1.5 py-0.5 font-mono tracking-widest uppercase">
                  {t('levelTestLabel')}
                </span>
              )}
            </div>
          </div>
          {!status.locked && (
            <span className="text-fl-hint text-fl-muted-3 shrink-0 font-mono">
              {barWidth}%
            </span>
          )}
        </div>

        {/* Progress bar */}
        {!status.locked && (
          <div className="bg-fl-border h-px">
            <div
              className="bg-fl-fg h-px transition-all duration-500"
              style={{ width: `${barWidth}%` }}
            />
          </div>
        )}
      </button>

      {/* EMPEZAR CTA — only shown on the active unit when a lesson is ready */}
      {status.active && onStartLesson && (
        <div className="border-fl-fg/30 flex flex-wrap items-center justify-end gap-2 border-t px-4 py-2.5">
          {/* Fork: voice launch options for today's lesson, beside START */}
          {onRoleplayLesson && (
            <button
              onClick={onRoleplayLesson}
              className="text-fl-muted-3 hover:text-fl-fg px-3 py-2 font-mono text-xs font-bold tracking-widest uppercase transition-colors"
            >
              {t('useInScenario')}
            </button>
          )}
          {onPracticeLesson && (
            <button
              onClick={onPracticeLesson}
              className="border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg border px-3 py-2 font-mono text-xs font-bold tracking-widest uppercase transition-colors"
            >
              {t('practiceWithLingu')}
            </button>
          )}
          <button
            onClick={onStartLesson}
            className="bg-fl-fg text-fl-bg hover:bg-fl-fg/90 px-4 py-2 font-mono text-xs font-bold tracking-widest uppercase transition-colors"
          >
            {tCommon('start')} →
          </button>
        </div>
      )}
    </div>
  )
}
