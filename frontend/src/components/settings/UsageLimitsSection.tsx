'use client'

// Fork: the usage-limits section is self-serve — learners on a self-hosted
// install set their own quotas (0 = unlimited). With billing enabled the
// backend rejects non-admin edits, so the editor is hidden there too.

import { useState, useEffect, useCallback } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { useConfigStore } from '@/store/config'
import { type QuotaStatus } from '@/types/api'

interface LimitRow {
  key: string
  label: string
  used: number
  limit: number
  unlimited: boolean
  format: (v: number) => string
  /** UserUpdateRequest field this row writes to */
  field: string
  /** convert the input value to the stored unit (e.g. k tokens → tokens) */
  toStored: (v: number) => number
  /** convert the stored limit to the input unit */
  toInput: (v: number) => number
}

export function UsageLimitsSection({ title }: { title?: string } = {}) {
  const t = useTranslations('settings')
  const user = useAuthStore((s) => s.user)
  const stripeEnabled = useConfigStore((s) => s.stripeEnabled)
  const [quota, setQuota] = useState<QuotaStatus | null>(null)
  const [editing, setEditing] = useState(false)
  // input-unit values; null = unlimited
  const [draft, setDraft] = useState<Record<string, number | null>>({})
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<'saved' | 'error' | null>(null)

  const loadQuota = useCallback(() => {
    apiFetch('/api/auth/quota')
      .then((r) => r.json())
      .then((data: QuotaStatus) => setQuota(data))
      .catch(() => {
        /* silently ignore — section stays in skeleton state */
      })
  }, [])

  useEffect(() => {
    loadQuota()
  }, [loadQuota])

  const canEdit = !stripeEnabled || user?.role === 'admin'

  const rows: LimitRow[] = quota
    ? [
        {
          key: 'sessions',
          label: t('quotaSessions'),
          used: quota.sessions_this_week,
          limit: quota.sessions_limit,
          unlimited: quota.sessions_unlimited,
          format: (v: number) => String(v),
          field: 'conversation_weekly_sessions',
          toStored: (v) => v,
          toInput: (v) => v,
        },
        {
          key: 'minutesDay',
          label: t('quotaMinutesDay'),
          used: quota.minutes_today,
          limit: quota.minutes_limit,
          unlimited: quota.time_unlimited,
          format: (v: number) => String(v),
          field: 'conversation_daily_minutes',
          toStored: (v) => v,
          toInput: (v) => v,
        },
        {
          key: 'minutesWeek',
          label: t('quotaMinutesWeek'),
          used: quota.minutes_this_week,
          limit: quota.weekly_minutes_limit,
          unlimited: quota.weekly_minutes_unlimited,
          format: (v: number) => String(v),
          field: 'conversation_weekly_minutes',
          toStored: (v) => v,
          toInput: (v) => v,
        },
        {
          key: 'tokens',
          label: t('quotaTokens'),
          used: Math.round((quota.tokens_this_month ?? 0) / 1000),
          limit: Math.round((quota.tokens_monthly_limit ?? 0) / 1000),
          unlimited: quota.tokens_unlimited ?? false,
          format: (v: number) => `${v}k`,
          field: 'monthly_tokens_limit',
          toStored: (v) => v * 1000,
          toInput: (v) => Math.round(v / 1000),
        },
      ]
    : []

  function startEditing() {
    setDraft(
      Object.fromEntries(
        rows.map((r) => [r.field, r.unlimited ? null : r.toInput(r.limit)])
      )
    )
    setSaveState(null)
    setEditing(true)
  }

  async function saveLimits() {
    setSaving(true)
    setSaveState(null)
    try {
      const body: Record<string, number> = {}
      for (const row of rows) {
        const value = draft[row.field]
        body[row.field] = value === null ? 0 : row.toStored(Math.max(0, value))
      }
      const res = await apiFetch('/api/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error('save_failed')
      setEditing(false)
      setSaveState('saved')
      loadQuota()
    } catch {
      setSaveState('error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-fl-border bg-fl-surface border p-6">
      <div className="border-fl-border mb-5 flex items-center justify-between gap-2 border-b pb-4">
        <div className="flex items-center gap-2">
          <span className="text-fl-label text-fl-muted-2">●</span>
          <span className="text-fl-label text-fl-muted-2 font-mono tracking-widest uppercase">
            {title ?? t('sectionUsageLimits')}
          </span>
        </div>
        {quota !== null && canEdit && !editing && (
          <button
            onClick={startEditing}
            className="text-fl-label text-fl-muted-2 hover:text-fl-fg font-mono tracking-widest uppercase transition-colors"
          >
            {t('quotaEditLimits')}
          </button>
        )}
      </div>
      {quota === null ? (
        <div className="animate-pulse space-y-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="bg-fl-surface-2 h-4" />
          ))}
        </div>
      ) : editing ? (
        <div className="space-y-3">
          {rows.map((row) => {
            const value = draft[row.field]
            const unlimited = value === null
            return (
              <div key={row.key} className="flex items-center gap-3">
                <span className="text-fl-hint text-fl-muted-4 w-36 shrink-0 font-mono tracking-widest uppercase">
                  {row.label}
                </span>
                <input
                  type="number"
                  min={1}
                  disabled={unlimited}
                  value={unlimited ? '' : value}
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10)
                    setDraft((prev) => ({
                      ...prev,
                      [row.field]: Number.isNaN(n) ? 0 : n,
                    }))
                  }}
                  className="border-fl-border bg-fl-surface-2 text-fl-fg w-24 border px-2 py-1 font-mono text-xs tabular-nums disabled:opacity-40"
                />
                {row.key === 'tokens' && (
                  <span className="text-fl-hint text-fl-muted-3 font-mono">k</span>
                )}
                <label className="text-fl-hint text-fl-muted-2 flex items-center gap-1.5 font-mono">
                  <input
                    type="checkbox"
                    checked={unlimited}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        [row.field]: e.target.checked
                          ? null
                          : row.unlimited
                            ? 0
                            : row.toInput(row.limit),
                      }))
                    }
                  />
                  {t('quotaUnlimited')}
                </label>
              </div>
            )
          })}
          {saveState === 'error' && (
            <p className="text-fl-hint text-fl-error font-mono">
              {t('saveFailed')}
            </p>
          )}
          <div className="flex gap-2 pt-1">
            <button
              onClick={() => void saveLimits()}
              disabled={saving}
              className="text-fl-label bg-fl-fg text-fl-bg hover:bg-fl-fg/90 px-3 py-1.5 font-mono font-bold tracking-widest uppercase transition-colors disabled:opacity-50"
            >
              {saving ? '…' : t('saveChanges')}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={saving}
              className="text-fl-label text-fl-muted-2 hover:text-fl-fg px-3 py-1.5 font-mono font-bold tracking-widest uppercase transition-colors"
            >
              {t('quotaCancel')}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map(({ key, label, used, limit, unlimited, format }) => {
            const pct =
              unlimited || limit === 0
                ? null
                : Math.min(100, Math.round((used / limit) * 100))
            const exceeded = !unlimited && limit > 0 && used >= limit
            return (
              <div key={key} className="flex items-center gap-3">
                <span className="text-fl-hint text-fl-muted-4 w-36 shrink-0 font-mono tracking-widest uppercase">
                  {label}
                </span>
                {unlimited ? (
                  <span className="text-fl-hint text-fl-muted-2 font-mono">
                    {t('quotaUnlimited')}
                  </span>
                ) : (
                  <>
                    <div className="bg-fl-surface-2 h-1 flex-1 overflow-hidden">
                      <div
                        className={`h-full transition-all ${exceeded ? 'bg-fl-error' : 'bg-fl-accent'}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span
                      className={`text-fl-hint font-mono tabular-nums ${exceeded ? 'text-fl-error' : 'text-fl-muted-2'}`}
                    >
                      {format(used)}&thinsp;/&thinsp;{format(limit)}
                    </span>
                  </>
                )}
              </div>
            )
          })}
          {saveState === 'saved' && (
            <p className="text-fl-hint text-fl-success font-mono">{t('saved')}</p>
          )}
          <p className="text-fl-hint text-fl-muted-3 pt-1 font-mono">
            {t('quotaHint')}
          </p>
        </div>
      )}
    </div>
  )
}
