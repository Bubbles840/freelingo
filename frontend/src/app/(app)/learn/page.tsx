'use client'

// Fork: "Learn with Lingu" — a dedicated home for voice lessons, so guided
// lessons and roleplay are launchable without digging through My Plan.

import { useCallback, useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { useLanguageStore } from '@/store/language'

// Voice stack is browser-only (VAD/WASM), same as the conversation page.
const ConversationMode = dynamic(
  () => import('@/components/conversation/ConversationMode'),
  { ssr: false }
)

interface VoiceLesson {
  id: number
  title: string
  lesson_type: string
  week: number
  day: number
  completed: boolean
}

export default function LearnWithLinguPage() {
  const t = useTranslations('plan')
  const activeLanguage = useLanguageStore((s) => s.activeLanguage)
  // Fork: the lesson runs INSIDE this tab rather than bouncing to /conversation
  const [activeLesson, setActiveLesson] = useState<{
    id: number
    mode: 'guided' | 'roleplay'
  } | null>(null)
  const [cefrLevel, setCefrLevel] = useState<string | null>(null)

  const [today, setToday] = useState<VoiceLesson[]>([])
  const [pending, setPending] = useState<VoiceLesson[]>([])
  const [todayPending, setTodayPending] = useState(true)
  const [loaded, setLoaded] = useState(false)

  const lessonTypeLabel: Record<string, string> = {
    grammar: t('lessonTypes.grammar'),
    vocabulary: t('lessonTypes.vocabulary'),
    reading: t('lessonTypes.reading'),
    writing: t('lessonTypes.writing'),
    listening: t('lessonTypes.listening'),
    review: t('lessonTypes.review'),
    conversation: t('lessonTypes.conversation'),
  }

  const load = useCallback(() => {
    setLoaded(false)
    void apiFetch('/api/study-plan/pending-lessons')
      .then(async (res) => {
        if (!res.ok) return
        const data = (await res.json()) as {
          id: number
          title: string
          lesson_type: string
          week_number: number
          day_number: number
        }[]
        setPending(
          data.map((l) => ({
            id: l.id,
            title: l.title,
            lesson_type: l.lesson_type,
            week: l.week_number,
            day: l.day_number,
            completed: false,
          }))
        )
      })
      .catch(() => {})
      .finally(() => setLoaded(true))

    // /today lazily generates missing lessons via the LLM — can take minutes
    // on a new day, so it must never block the page.
    setTodayPending(true)
    void apiFetch('/api/study-plan/today')
      .then(async (res) => {
        if (!res.ok) return
        const data = (await res.json()) as {
          cefr_level?: string
          lessons: {
            id: number | null
            title: string
            lesson_type: string
            week: number
            day: number
            is_completed?: boolean
          }[]
        }
        if (data.cefr_level) setCefrLevel(data.cefr_level)
        setToday(
          data.lessons
            .filter((l): l is typeof l & { id: number } => l.id != null)
            .map((l) => ({
              id: l.id,
              title: l.title,
              lesson_type: l.lesson_type,
              week: l.week,
              day: l.day,
              completed: l.is_completed ?? false,
            }))
        )
      })
      .catch(() => {})
      .finally(() => setTodayPending(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-fetch when language changes
  }, [activeLanguage?.code])

  useEffect(() => {
    load()
  }, [load])

  // Fork: accept a lesson handed off from My Plan (same sessionStorage
  // contract the conversation page uses) and launch it inline here.
  useEffect(() => {
    const raw = sessionStorage.getItem('voice_lesson')
    if (!raw) return
    sessionStorage.removeItem('voice_lesson')
    try {
      const parsed = JSON.parse(raw) as { lessonId?: unknown; mode?: unknown }
      if (
        typeof parsed.lessonId === 'number' &&
        Number.isFinite(parsed.lessonId) &&
        parsed.lessonId > 0
      ) {
        setActiveLesson({
          id: parsed.lessonId,
          mode: parsed.mode === 'roleplay' ? 'roleplay' : 'guided',
        })
      }
    } catch {
      // malformed — ignore
    }
  }, [])

  function launch(lessonId: number, mode: 'guided' | 'roleplay') {
    setActiveLesson({ id: lessonId, mode })
  }

  if (activeLesson) {
    return (
      <div className="h-full">
        <ConversationMode
          lessonId={activeLesson.id}
          lessonMode={activeLesson.mode}
          autoStart
          cefrLevel={cefrLevel}
          targetLanguage={activeLanguage?.code}
          pageTitle={t('learnTitle')}
          pageSubtitle={t('learnSubtitle')}
          onClose={() => {
            setActiveLesson(null)
            load() // refresh completion states after the session
          }}
        />
      </div>
    )
  }

  function LessonRow({ lesson }: { lesson: VoiceLesson }) {
    return (
      <div className="border-fl-border bg-fl-surface flex flex-wrap items-center gap-3 border px-5 py-4">
        <span
          className={`w-4 shrink-0 font-mono text-base ${lesson.completed ? 'text-fl-fg' : 'text-fl-muted-3'}`}
        >
          {lesson.completed ? '✓' : '○'}
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={`text-fl-label font-mono ${lesson.completed ? 'text-fl-muted-2' : 'text-fl-muted-1'}`}
          >
            {lesson.title}
          </p>
          <p className="text-fl-hint text-fl-muted-3 mt-0.5 font-mono">
            {t('weekDay', { week: lesson.week, day: lesson.day })} ·{' '}
            {lessonTypeLabel[lesson.lesson_type] ?? lesson.lesson_type}
          </p>
        </div>
        <div className="flex basis-full flex-wrap items-center gap-2 pl-7 sm:basis-auto sm:pl-0">
          <button
            onClick={() => launch(lesson.id, 'guided')}
            className="text-fl-label border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg min-w-24 shrink-0 border px-3 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
          >
            {t('practiceWithLingu')}
          </button>
          <button
            onClick={() => launch(lesson.id, 'roleplay')}
            className="text-fl-label text-fl-muted-3 hover:text-fl-fg min-w-24 shrink-0 px-3 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
          >
            {t('useInScenario')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="border-fl-border mb-6 border-b pb-4">
        <p className="text-fl-label text-fl-muted-2 mb-1 font-mono tracking-widest uppercase">
          {t('learnSubtitle')}
        </p>
        <h1 className="text-fl-fg font-mono text-2xl font-bold tracking-tight">
          {t('learnTitle')}
        </h1>
      </div>

      <h2 className="text-fl-label text-fl-muted-2 mb-3 font-mono tracking-widest uppercase">
        {t('learnToday')}
      </h2>
      {todayPending && (
        <p className="text-fl-hint text-fl-muted-3 mb-3 animate-pulse font-mono">
          ● {t('preparingToday')}
        </p>
      )}
      <div className="mb-8 space-y-3">
        {today.map((l) => (
          <LessonRow key={l.id} lesson={l} />
        ))}
        {!todayPending && today.length === 0 && (
          <p className="text-fl-hint text-fl-muted-3 font-mono">
            {t('learnNone')}
          </p>
        )}
      </div>

      {pending.length > 0 && (
        <>
          <h2 className="text-fl-label text-fl-muted-2 mb-3 font-mono tracking-widest uppercase">
            {t('learnCatchUp')}
          </h2>
          <div className="space-y-3">
            {pending.map((l) => (
              <LessonRow key={l.id} lesson={l} />
            ))}
          </div>
        </>
      )}

      {!loaded && today.length === 0 && pending.length === 0 && (
        <p className="text-fl-hint text-fl-muted-3 font-mono">…</p>
      )}
    </div>
  )
}
