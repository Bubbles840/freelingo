import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import TranscriptBubble from '@/components/conversation/TranscriptBubble'

// Interpolation-aware next-intl mock, matching the pattern already used in
// tests/components/LandingReviewsCarousel.test.tsx (the house convention's
// variant for components that render translated placeholder values, as
// opposed to the plain `key => key` variant used elsewhere).
vi.mock('next-intl', () => ({
  useTranslations:
    () => (key: string, values?: Record<string, string | number>) => {
      if (key === 'pronunciationScore') return `Pronunciation ${values?.score}/100`
      return key
    },
}))

const info = {
  overall: 62,
  fluency: 84,
  words: [
    { word: 'Hola', score: 95 },
    { word: 'bolígrafo', score: 41 },
  ],
}

describe('TranscriptBubble pronunciation', () => {
  it('renders no score chip when there is no assessment', () => {
    render(<TranscriptBubble role="user" text="Hola bolígrafo" />)
    expect(screen.queryByTestId('pronunciation-score')).toBeNull()
  })

  it('renders the overall score for a user bubble', () => {
    render(
      <TranscriptBubble role="user" text="Hola bolígrafo" pronunciation={info} />
    )
    expect(screen.getByTestId('pronunciation-score').textContent).toContain('62')
  })

  it('underlines weak words red and strong words green in the bubble text', () => {
    render(
      <TranscriptBubble role="user" text="Hola bolígrafo" pronunciation={info} />
    )
    const weak = screen.getByText('bolígrafo')
    expect(weak.className).toContain('decoration-red-800')
    expect(weak.className).toContain('decoration-wavy')
    const strong = screen.getByText('Hola')
    expect(strong.className).toContain('decoration-green-800')
  })

  it('matches words case- and punctuation-insensitively', () => {
    render(
      <TranscriptBubble
        role="user"
        text="¿Cómo te llamas?"
        pronunciation={{
          overall: 90,
          fluency: 90,
          words: [
            { word: 'cómo', score: 92 },
            { word: 'te', score: 88 },
            { word: 'llamas', score: 30 },
          ],
        }}
      />
    )
    expect(screen.getByText('¿Cómo').className).toContain('decoration-green-800')
    expect(screen.getByText('llamas?').className).toContain('decoration-red-800')
  })

  it('falls back to plain text when scored words do not match the transcript', () => {
    render(
      <TranscriptBubble
        role="user"
        text="Como te amo."
        pronunciation={{
          overall: 40,
          fluency: 40,
          words: [
            { word: 'coma', score: 30 },
            { word: 'usted', score: 20 },
            { word: 'mau', score: 10 },
          ],
        }}
      />
    )
    // Misaligned assessment must not paint any word — but the overall
    // score chip still shows.
    expect(document.querySelector('[class*="decoration-"]')).toBeNull()
    expect(screen.getByTestId('pronunciation-score')).toBeTruthy()
  })

  it('leaves mid-range words undecorated', () => {
    render(
      <TranscriptBubble
        role="user"
        text="Hola"
        pronunciation={{
          overall: 70,
          fluency: 70,
          words: [{ word: 'Hola', score: 70 }],
        }}
      />
    )
    // 60–79 band: aligned but visually neutral, wrapped in a plain span.
    const word = screen.getByText('Hola', { selector: 'span' })
    expect(word.className).not.toContain('decoration-')
  })

  it('never renders an assessment on an assistant bubble', () => {
    render(
      <TranscriptBubble role="assistant" text="Muy bien" pronunciation={info} />
    )
    expect(screen.queryByTestId('pronunciation-score')).toBeNull()
  })
})
