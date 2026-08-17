import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useProgressStore } from '@/store/progress'

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(),
}))

describe('useLessonCompletion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useProgressStore.setState({ completedToday: [] })
  })

  it('a failed POST leaves status "error" and does NOT touch the progress store', async () => {
    const { apiFetch } = await import('@/lib/api')
    vi.mocked(apiFetch).mockResolvedValueOnce(
      new Response(null, { status: 500 })
    )

    const { useLessonCompletion } = await import(
      '@/hooks/useLessonCompletion'
    )
    const { result } = renderHook(() => useLessonCompletion())

    await act(async () => {
      await result.current.complete(42)
    })

    expect(result.current.status).toBe('error')
    expect(apiFetch).toHaveBeenCalledTimes(1)
    expect(useProgressStore.getState().completedToday).not.toContain(42)
  })

  it('retry after a failure issues exactly one more POST and updates the store once on success', async () => {
    const { apiFetch } = await import('@/lib/api')
    vi.mocked(apiFetch)
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))

    const { useLessonCompletion } = await import(
      '@/hooks/useLessonCompletion'
    )
    const { result } = renderHook(() => useLessonCompletion())

    await act(async () => {
      await result.current.complete(7)
    })
    expect(result.current.status).toBe('error')
    expect(apiFetch).toHaveBeenCalledTimes(1)

    await act(async () => {
      await result.current.retry()
    })

    expect(apiFetch).toHaveBeenCalledTimes(2)
    expect(result.current.status).toBe('completed')
    expect(
      useProgressStore
        .getState()
        .completedToday.filter((id) => id === 7)
    ).toHaveLength(1)
  })

  it('a duplicate completion call after a SUCCESSFUL completion does not issue a second POST', async () => {
    const { apiFetch } = await import('@/lib/api')
    vi.mocked(apiFetch).mockResolvedValueOnce(
      new Response(null, { status: 200 })
    )

    const { useLessonCompletion } = await import(
      '@/hooks/useLessonCompletion'
    )
    const { result } = renderHook(() => useLessonCompletion())

    await act(async () => {
      await result.current.complete(9)
    })
    expect(result.current.status).toBe('completed')
    expect(apiFetch).toHaveBeenCalledTimes(1)

    // A duplicate `lesson_completed` frame re-invokes complete() with the
    // same lesson id — the guard must still be latched from the successful
    // call, so no second POST goes out.
    await act(async () => {
      await result.current.complete(9)
    })

    expect(apiFetch).toHaveBeenCalledTimes(1)
    expect(
      useProgressStore
        .getState()
        .completedToday.filter((id) => id === 9)
    ).toHaveLength(1)
  })
})
