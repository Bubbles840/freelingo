'use client'

// Fork: options dialog for exporting cards to an Anki .apkg deck.

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'

const CARD_TYPES = ['basic', 'basic_reversed', 'cloze'] as const
type CardType = (typeof CARD_TYPES)[number]

export default function AnkiExportDialog({
  endpoint,
  defaultDeckName,
  onClose,
}: {
  /** POST target, e.g. /api/anki/flashcards or /api/anki/lessons/12 */
  endpoint: string
  defaultDeckName: string
  onClose: () => void
}) {
  const t = useTranslations('flashcards')
  const [deckName, setDeckName] = useState(defaultDeckName)
  const [cardType, setCardType] = useState<CardType>('basic')
  const [includeDefinition, setIncludeDefinition] = useState(true)
  const [includeExample, setIncludeExample] = useState(true)
  const [enrich, setEnrich] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)

  const typeLabel: Record<CardType, string> = {
    basic: t('ankiTypeBasic'),
    basic_reversed: t('ankiTypeReversed'),
    cloze: t('ankiTypeCloze'),
  }

  async function doExport() {
    setBusy(true)
    setError(false)
    try {
      const res = await apiFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          deck_name: deckName,
          card_type: cardType,
          include_definition: includeDefinition,
          include_example: includeExample,
          enrich,
        }),
      })
      if (!res.ok) throw new Error('export_failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${deckName.replace(/[^\w\- ]+/g, '_').trim() || 'freelingo'}.apkg`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      onClose()
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="border-fl-border bg-fl-surface w-full max-w-md border p-6">
        <h2 className="text-fl-fg mb-4 font-mono text-base font-bold tracking-wide">
          {t('exportToAnki')}
        </h2>

        <label className="text-fl-label text-fl-muted-2 mb-1 block font-mono tracking-widest uppercase">
          {t('ankiDeckName')}
        </label>
        <input
          value={deckName}
          onChange={(e) => setDeckName(e.target.value)}
          maxLength={100}
          className="border-fl-border bg-fl-surface-2 text-fl-fg mb-4 w-full border px-3 py-2 font-mono text-sm"
        />

        <p className="text-fl-label text-fl-muted-2 mb-1 font-mono tracking-widest uppercase">
          {t('ankiCardType')}
        </p>
        <div className="mb-4 flex flex-col gap-1">
          {CARD_TYPES.map((type) => (
            <label
              key={type}
              className="text-fl-muted-1 flex items-center gap-2 font-mono text-xs"
            >
              <input
                type="radio"
                name="anki-card-type"
                checked={cardType === type}
                onChange={() => setCardType(type)}
              />
              {typeLabel[type]}
            </label>
          ))}
        </div>

        <div className="mb-4 flex flex-col gap-1">
          <label className="text-fl-muted-1 flex items-center gap-2 font-mono text-xs">
            <input
              type="checkbox"
              checked={includeDefinition}
              onChange={(e) => setIncludeDefinition(e.target.checked)}
            />
            {t('ankiIncludeDefinition')}
          </label>
          <label className="text-fl-muted-1 flex items-center gap-2 font-mono text-xs">
            <input
              type="checkbox"
              checked={includeExample}
              onChange={(e) => setIncludeExample(e.target.checked)}
            />
            {t('ankiIncludeExample')}
          </label>
          <label className="text-fl-muted-1 flex items-center gap-2 font-mono text-xs">
            <input
              type="checkbox"
              checked={enrich}
              onChange={(e) => setEnrich(e.target.checked)}
            />
            {t('ankiAiExpand')}
          </label>
          {enrich && (
            <p className="text-fl-hint text-fl-muted-3 pl-6 font-mono">
              {t('ankiAiExpandHint')}
            </p>
          )}
        </div>

        {error && (
          <p className="text-fl-hint text-fl-error mb-3 font-mono">
            {t('ankiExportFailed')}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="text-fl-label text-fl-muted-2 hover:text-fl-fg px-4 py-2 font-mono font-bold tracking-widest uppercase transition-colors"
          >
            {t('ankiCancel')}
          </button>
          <button
            onClick={() => void doExport()}
            disabled={busy}
            className="text-fl-label bg-fl-fg text-fl-bg hover:bg-fl-fg/90 px-4 py-2 font-mono font-bold tracking-widest uppercase transition-colors disabled:opacity-50"
          >
            {busy ? '…' : t('ankiExport')}
          </button>
        </div>
      </div>
    </div>
  )
}
