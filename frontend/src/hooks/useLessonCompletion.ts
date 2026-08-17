'use client'

import { useCallback, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { useProgressStore } from '@/store/progress'
import { getLogger } from '@/lib/logger'

// Always-on (not gated by any UI debug flag): lesson completion writes real
// XP, so failures must be visible in production, not just in dev.
const lessonLogger = getLogger('conversation-lesson')

export type LessonCompletionStatus = 'idle' | 'completed' | 'error'

/**
 * Tracks the client-side write-back for a guided lesson's completion
 * (`POST /api/lessons/{id}/complete` → `useProgressStore.completeLesson`).
 *
 * Re-entrancy: while a request is in flight, or after it has *succeeded*,
 * further `complete()` calls for the session are no-ops — this is what
 * makes a duplicate `lesson_completed` WS frame result in exactly one POST.
 *
 * Failure handling: a failed POST (bad response or thrown error) releases
 * the guard and moves to `'error'` status instead of staying silently
 * stuck — a failed request must not permanently lose the learner's XP.
 * `retry()` re-issues the same request for the same lesson id.
 */
export function useLessonCompletion() {
  const [status, setStatus] = useState<LessonCompletionStatus>('idle')
  const completingRef = useRef(false)
  const lastLessonIdRef = useRef<number | null>(null)

  const complete = useCallback(async (lessonId: number) => {
    if (completingRef.current) return
    completingRef.current = true
    lastLessonIdRef.current = lessonId
    try {
      const res = await apiFetch(`/api/lessons/${lessonId}/complete`, {
        method: 'POST',
      })
      if (!res.ok) {
        lessonLogger.warn('lesson completion request failed', {
          status: res.status,
        })
        completingRef.current = false
        setStatus('error')
        return
      }
      useProgressStore.getState().completeLesson(lessonId)
      setStatus('completed')
    } catch (error) {
      // Failure must not break the voice session — log and continue, but
      // release the guard so a retry (or a later legitimate attempt) can
      // still succeed instead of silently dropping the completion forever.
      lessonLogger.error('lesson completion request failed', {
        error: error instanceof Error ? error.message : String(error),
      })
      completingRef.current = false
      setStatus('error')
    }
  }, [])

  const retry = useCallback((): Promise<void> => {
    const lessonId = lastLessonIdRef.current
    if (lessonId === null) return Promise.resolve()
    return complete(lessonId)
  }, [complete])

  const reset = useCallback(() => {
    completingRef.current = false
    lastLessonIdRef.current = null
    setStatus('idle')
  }, [])

  return { status, complete, retry, reset }
}
