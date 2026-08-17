import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import LessonPanel, {
  type LessonPanelStep,
} from '@/components/conversation/LessonPanel'

vi.mock('next-intl', () => ({
  useTranslations:
    () => (key: string, values?: Record<string, string | number>) => {
      if (key === 'lessonStepProgress') {
        return `Step ${values?.current} of ${values?.total}`
      }
      return key
    },
}))

const steps: LessonPanelStep[] = [
  { title: 'Greetings', status: 'done' },
  { title: 'Present tense', status: 'current' },
  { title: 'Question words', status: 'pending' },
  { title: 'Practice dialogue', status: 'pending' },
  { title: 'Review', status: 'pending' },
]

function renderPanel(
  overrides: Partial<{
    visible: boolean
    onToggle: () => void
    onAdvance: () => void
  }> = {}
) {
  const onToggle = overrides.onToggle ?? vi.fn()
  const onAdvance = overrides.onAdvance ?? vi.fn()
  render(
    <LessonPanel
      title="Present Tense Basics"
      steps={steps}
      stepIndex={1}
      total={5}
      visible={overrides.visible ?? true}
      onToggle={onToggle}
      onAdvance={onAdvance}
    />
  )
  return { onToggle, onAdvance }
}

describe('LessonPanel', () => {
  it('renders every step title', () => {
    renderPanel()
    for (const step of steps) {
      expect(screen.getByText(step.title)).toBeInTheDocument()
    }
  })

  it('marks the current step with aria-current="step"', () => {
    renderPanel()
    const currentItem = screen.getByText('Present tense').closest('li')
    expect(currentItem).toHaveAttribute('aria-current', 'step')
    const doneItem = screen.getByText('Greetings').closest('li')
    expect(doneItem).not.toHaveAttribute('aria-current')
  })

  it('renders done/pending states distinctly', () => {
    renderPanel()
    expect(screen.getByTestId('lesson-step-done')).toBeInTheDocument()
    expect(screen.getAllByTestId('lesson-step-pending')).toHaveLength(3)
    expect(screen.getByTestId('lesson-step-current')).toBeInTheDocument()
  })

  it('shows "step 2 of 5" style progress text', () => {
    renderPanel()
    expect(screen.getByText('Step 2 of 5')).toBeInTheDocument()
  })

  it('clicking "next section" calls onAdvance', () => {
    const { onAdvance } = renderPanel()
    fireEvent.click(screen.getByText('lessonNextSection'))
    expect(onAdvance).toHaveBeenCalledTimes(1)
  })

  it('when visible=false, step titles are not rendered but the progress text is', () => {
    renderPanel({ visible: false })
    expect(screen.queryByText('Greetings')).toBeNull()
    expect(screen.queryByText('Present tense')).toBeNull()
    expect(screen.getByText('Step 2 of 5')).toBeInTheDocument()
  })

  it('clicking the toggle calls onToggle', () => {
    const { onToggle } = renderPanel({ visible: true })
    fireEvent.click(screen.getByText('lessonHide'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
