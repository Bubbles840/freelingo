import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import UnitDrawer from '@/components/plan/UnitDrawer'
import type { CurriculumUnit } from '@/data/curriculum'

// ── Mocks ──────────────────────────────────────────────────────────────────

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

// ── Fixtures ───────────────────────────────────────────────────────────────

const mockUnit: CurriculumUnit = {
  id: 'unit-a1-1',
  level: 'A1',
  unit_number: 1,
  title: 'Greetings & Introductions',
  default_weeks: 2,
  grammar_points: ['presente de indicativo'],
  vocabulary_set_ids: ['v1'],
  lesson_types: ['grammar', 'vocabulary'],
  prerequisite_unit: undefined,
  competency_checklist: ['Can greet people'],
}

type FixtureLesson = {
  id: number | null
  title: string
  lesson_type: string
  week: number
  day: number
  completed: boolean
  action?: 'start' | 'continue' | 'review'
}

const lessonWithId: FixtureLesson = {
  id: 3,
  title: 'Reading Comprehension',
  lesson_type: 'reading',
  week: 2,
  day: 1,
  completed: false,
  action: 'start',
}

// Generated, but neither today's lesson nor in progress: `plan/page.tsx`
// deliberately leaves `action` undefined so upstream shows no button at all
// — the roleplay button must respect the same gate as the practice button.
const generatedLessonWithoutAction: FixtureLesson = {
  id: 8,
  title: 'Future Unit Lesson',
  lesson_type: 'vocabulary',
  week: 5,
  day: 2,
  completed: false,
}

function renderDrawer(
  lessons: FixtureLesson[],
  overrides: Partial<{
    onClose: () => void
    onStartLesson: (lessonId: number) => void
    onPracticeLesson: (lessonId: number) => void
    onRoleplayLesson: (lessonId: number) => void
  }> = {}
) {
  const props = {
    unit: mockUnit,
    lessons,
    onClose: overrides.onClose ?? vi.fn(),
    onStartLesson: overrides.onStartLesson ?? vi.fn(),
    onPracticeLesson: overrides.onPracticeLesson ?? vi.fn(),
    onRoleplayLesson: overrides.onRoleplayLesson ?? vi.fn(),
  }
  render(<UnitDrawer {...props} />)
  return props
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('UnitDrawer — "use it in a scenario" (roleplay) launch button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a "use it in a scenario" button for a lesson with an id and an action', () => {
    renderDrawer([lessonWithId])
    expect(screen.getByText('useInScenario')).toBeInTheDocument()
  })

  it('does NOT render it when lesson.action is undefined (same gate as the practice button)', () => {
    renderDrawer([generatedLessonWithoutAction])
    expect(screen.queryByText('useInScenario')).toBeNull()
  })

  it('clicking it calls onRoleplayLesson with the lesson id', () => {
    const onRoleplayLesson = vi.fn()
    renderDrawer([lessonWithId], { onRoleplayLesson })

    fireEvent.click(screen.getByText('useInScenario'))

    expect(onRoleplayLesson).toHaveBeenCalledTimes(1)
    expect(onRoleplayLesson).toHaveBeenCalledWith(3)
  })

  it('clicking it does NOT call onPracticeLesson or onStartLesson', () => {
    const onStartLesson = vi.fn()
    const onPracticeLesson = vi.fn()
    const onRoleplayLesson = vi.fn()
    renderDrawer([lessonWithId], {
      onStartLesson,
      onPracticeLesson,
      onRoleplayLesson,
    })

    fireEvent.click(screen.getByText('useInScenario'))

    expect(onRoleplayLesson).toHaveBeenCalledTimes(1)
    expect(onPracticeLesson).not.toHaveBeenCalled()
    expect(onStartLesson).not.toHaveBeenCalled()
  })

  it('the existing practice button still calls onPracticeLesson (no regression)', () => {
    const onPracticeLesson = vi.fn()
    const onRoleplayLesson = vi.fn()
    renderDrawer([lessonWithId], { onPracticeLesson, onRoleplayLesson })

    fireEvent.click(screen.getByText('practiceWithLingu'))

    expect(onPracticeLesson).toHaveBeenCalledTimes(1)
    expect(onPracticeLesson).toHaveBeenCalledWith(3)
    expect(onRoleplayLesson).not.toHaveBeenCalled()
  })
})
