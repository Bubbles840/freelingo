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

const lessonWithNullId: FixtureLesson = {
  id: null,
  title: 'Pending Content',
  lesson_type: 'review',
  week: 3,
  day: 1,
  completed: false,
}

// Generated, but neither today's lesson nor in progress: `plan/page.tsx`
// deliberately leaves `action` undefined so upstream shows no button at all.
const generatedLessonWithoutAction: FixtureLesson = {
  id: 8,
  title: 'Future Unit Lesson',
  lesson_type: 'vocabulary',
  week: 5,
  day: 2,
  completed: false,
}

const completedReviewLesson: FixtureLesson = {
  id: 1,
  title: 'Verb Conjugation',
  lesson_type: 'grammar',
  week: 1,
  day: 1,
  completed: true,
  action: 'review',
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

describe('UnitDrawer — practice with Lingu launch button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a "practice with Lingu" button for a lesson that has an id and an action', () => {
    renderDrawer([lessonWithId])
    expect(screen.getByText('practiceWithLingu')).toBeInTheDocument()
  })

  it('does NOT render it for a lesson row with a null id', () => {
    renderDrawer([lessonWithNullId])
    expect(screen.queryByText('practiceWithLingu')).toBeNull()
  })

  it('does NOT render it for a lesson the plan offers no action for', () => {
    // Voice practice completes a lesson for real XP, so it must stay behind
    // the same gate as the primary action — otherwise an unscheduled future
    // lesson can be completed early and advance the plan out of order.
    renderDrawer([generatedLessonWithoutAction])
    expect(screen.queryByText('practiceWithLingu')).toBeNull()
  })

  it('clicking it calls onPracticeLesson with the lesson id (and NOT onStartLesson)', () => {
    const onStartLesson = vi.fn()
    const onPracticeLesson = vi.fn()
    renderDrawer([lessonWithId], { onStartLesson, onPracticeLesson })

    fireEvent.click(screen.getByText('practiceWithLingu'))

    expect(onPracticeLesson).toHaveBeenCalledTimes(1)
    expect(onPracticeLesson).toHaveBeenCalledWith(3)
    expect(onStartLesson).not.toHaveBeenCalled()
  })

  it('clicking the existing primary button still calls onStartLesson (no regression)', () => {
    const onStartLesson = vi.fn()
    const onPracticeLesson = vi.fn()
    renderDrawer([lessonWithId], { onStartLesson, onPracticeLesson })

    fireEvent.click(screen.getByText('start →'))

    expect(onStartLesson).toHaveBeenCalledTimes(1)
    expect(onStartLesson).toHaveBeenCalledWith(3)
    expect(onPracticeLesson).not.toHaveBeenCalled()
  })

  it("the practice button is rendered for a completed ('review') lesson too", () => {
    const onPracticeLesson = vi.fn()
    renderDrawer([completedReviewLesson], { onPracticeLesson })

    const button = screen.getByText('practiceWithLingu')
    expect(button).toBeInTheDocument()

    fireEvent.click(button)
    expect(onPracticeLesson).toHaveBeenCalledWith(1)
  })
})
