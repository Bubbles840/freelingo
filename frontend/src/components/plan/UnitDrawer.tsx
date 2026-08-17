'use client'

import { useEffect, useRef } from 'react'
import { useTranslations } from 'next-intl'
import type { CurriculumUnit } from '@/data/curriculum'

interface Lesson {
  id: number | null
  title: string
  lesson_type: string
  week: number
  day: number
  completed: boolean
  action?: 'start' | 'continue' | 'review'
}

interface Props {
  unit: CurriculumUnit
  lessons: Lesson[]
  onClose: () => void
  onStartLesson: (lessonId: number) => void
  onPracticeLesson: (lessonId: number) => void
  onRoleplayLesson: (lessonId: number) => void
}

export default function UnitDrawer({
  unit,
  lessons,
  onClose,
  onStartLesson,
  onPracticeLesson,
  onRoleplayLesson,
}: Props) {
  const t = useTranslations('plan')
  const tCommon = useTranslations('common')
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const lessonTypeLabel: Record<string, string> = {
    grammar: t('lessonTypes.grammar'),
    vocabulary: t('lessonTypes.vocabulary'),
    reading: t('lessonTypes.reading'),
    writing: t('lessonTypes.writing'),
    conversation: t('lessonTypes.conversation'),
    review: t('lessonTypes.review'),
    level_test: t('lessonTypes.level_test'),
  }

  return (
    <div className="bg-fl-bg/80 fixed inset-0 z-50 flex items-end justify-center p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div
        ref={ref}
        className="border-fl-border bg-fl-surface max-h-[80vh] w-full overflow-y-auto border sm:max-w-xl"
      >
        {/* Header */}
        <div className="border-fl-border bg-fl-surface sticky top-0 z-10 flex items-center justify-between border-b px-6 py-4">
          <div>
            <span className="text-fl-hint text-fl-muted-3 font-mono tracking-widest uppercase">
              {unit.level} · {t('unitLabel')}
            </span>
            <p className="text-fl-body text-fl-fg mt-0.5 font-mono">
              {unit.title}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-fl-muted-3 hover:text-fl-fg font-mono text-lg leading-none transition-colors"
            aria-label={tCommon('close')}
          >
            ✕
          </button>
        </div>

        {/* Grammar points */}
        {unit.grammar_points.length > 0 && (
          <div className="border-fl-border border-b px-6 py-4">
            <p className="text-fl-hint text-fl-muted-3 mb-2 font-mono tracking-widest uppercase">
              {t('grammarCovered')}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {unit.grammar_points.map((gp) => (
                <span
                  key={gp}
                  className="text-fl-hint border-fl-border text-fl-muted-1 border px-2 py-1 font-mono"
                >
                  {gp}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Lessons */}
        <div>
          <div className="border-fl-border border-b px-6 py-3">
            <p className="text-fl-hint text-fl-muted-3 font-mono tracking-widest uppercase">
              {t('lessonsHeader', { count: lessons.length })}
            </p>
          </div>
          <div className="divide-fl-border divide-y">
            {lessons.length === 0 ? (
              <div className="px-6 py-6">
                <p className="text-fl-label text-fl-muted-3 font-mono">
                  {t('noLessons')}
                </p>
              </div>
            ) : (
              lessons.map((lesson, i) => (
                <div
                  key={lesson.id ?? i}
                  className={`flex flex-wrap items-center gap-3 gap-y-2 px-6 py-4 transition-colors ${lesson.action ? 'hover:bg-fl-surface-2' : ''}`}
                >
                  <span
                    className={`w-4 shrink-0 font-mono text-base ${lesson.completed ? 'text-fl-fg' : 'text-fl-muted-3'}`}
                  >
                    {lesson.completed ? '✓' : '○'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p
                      className={`text-fl-label font-mono ${lesson.completed ? 'text-fl-muted-2 line-through' : 'text-fl-muted-1'}`}
                    >
                      {lesson.title}
                    </p>
                    <p className="text-fl-hint text-fl-muted-3 mt-0.5 font-mono">
                      {t('weekDay', { week: lesson.week, day: lesson.day })} ·{' '}
                      {lessonTypeLabel[lesson.lesson_type] ??
                        lesson.lesson_type}
                    </p>
                  </div>
                  {lesson.id != null && lesson.action && (
                    // Fork: `basis-full` forces the action buttons onto their
                    // own row — as flex siblings of the `min-w-0 flex-1` title
                    // they would crush it to a sliver instead of wrapping.
                    // `pl-7` aligns them under the title (icon w-4 + gap-3).
                    <div className="flex basis-full flex-wrap items-center gap-2 pl-7">
                      <button
                        onClick={() => onStartLesson(lesson.id!)}
                        className="text-fl-label text-fl-bg bg-fl-fg hover:bg-fl-fg/90 min-w-24 shrink-0 px-3 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
                      >
                        {lesson.action === 'review'
                          ? t('reviewLesson')
                          : lesson.action === 'continue'
                            ? t('resume')
                            : `${tCommon('start')} →`}
                      </button>
                      {/* Fork (guided lessons): voice practice sits behind the
                          same gate as the primary action — a lesson upstream
                          offers no action for (generated, but not today's and
                          not in progress) must not be completable by voice,
                          which would write XP and advance the plan early. */}
                      <button
                        onClick={() => onPracticeLesson(lesson.id!)}
                        className="text-fl-label border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg min-w-24 shrink-0 border px-3 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
                      >
                        {t('practiceWithLingu')}
                      </button>
                      {/* Fork (roleplay): tertiary/quiet action — same launch
                          gate as the two buttons above (see comment on the
                          practice button), but visually de-emphasised since
                          it's a less common path than start/resume/practice. */}
                      <button
                        onClick={() => onRoleplayLesson(lesson.id!)}
                        className="text-fl-label text-fl-muted-3 hover:text-fl-fg min-w-24 shrink-0 px-3 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
                      >
                        {t('useInScenario')}
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Close */}
        <div className="border-fl-border bg-fl-surface sticky bottom-0 border-t px-6 py-4">
          <button
            onClick={onClose}
            className="border-fl-border text-fl-muted-2 hover:border-fl-border-2 hover:text-fl-fg w-full border py-3 font-mono text-xs tracking-widest uppercase transition-colors"
          >
            {tCommon('close')}
          </button>
        </div>
      </div>
    </div>
  )
}
