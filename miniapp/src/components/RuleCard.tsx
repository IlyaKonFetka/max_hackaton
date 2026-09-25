import { useRef, useState } from 'react'
import type { ApplicableRule, Status } from '../api'
import { haptic } from '../bridge'

interface Props {
  rule: ApplicableRule
  disabled?: boolean
  onStatus: (status: Status) => Promise<void>
  onComment: (comment: string) => Promise<void>
  onPhoto: (file: File) => Promise<void>
}

const STATUS_LABEL: Record<Status, string> = { ok: 'Соблюдается', violation: 'Нарушение', unknown: 'Не знаю' }
const SEVERITY: Record<string, string> = { high: 'важное', medium: 'среднее', low: 'низкое' }

export function RuleCard({ rule, disabled, onStatus, onComment, onPhoto }: Props) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [comment, setComment] = useState(rule.comment ?? '')
  const [err, setErr] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const setStatus = async (s: Status) => {
    if (disabled || busy) return
    setBusy(true)
    setErr(null)
    try {
      haptic('select')
      await onStatus(s)
    } catch (e) {
      setErr((e as Error).message)
      haptic('error')
    } finally {
      setBusy(false)
    }
  }

  const saveComment = async () => {
    if (comment === (rule.comment ?? '')) return
    try {
      await onComment(comment)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const pick = () => fileRef.current?.click()
  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(true)
    setErr(null)
    try {
      await onPhoto(f)
      haptic('success')
    } catch (er) {
      setErr((er as Error).message)
      haptic('error')
    } finally {
      setBusy(false)
    }
  }

  const src = rule.source
  return (
    <div className={`card ${rule.status ?? ''}`}>
      <p className="card-title">{rule.title}</p>
      <div className="meta">
        <span className="badge">{rule.period_label}</span>
        {rule.severity === 'high' && <span className="badge sev-high">{SEVERITY[rule.severity]}</span>}
        {rule.evidence === 'photo' && <span>нужно фото</span>}
        {rule.evidence === 'document' && <span>нужен документ</span>}
      </div>

      <div className="seg" role="group" aria-label="Статус">
        {(['ok', 'violation', 'unknown'] as Status[]).map((s) => (
          <button
            key={s}
            type="button"
            className={`${s} ${rule.status === s ? 'on' : ''}`}
            disabled={disabled || busy}
            onClick={() => setStatus(s)}
          >
            {STATUS_LABEL[s]}
          </button>
        ))}
      </div>

      <button type="button" className="linkbtn" onClick={() => setOpen((v) => !v)}>
        {open ? 'Скрыть основание' : 'Что проверить и основание'}
      </button>
      {open && (
        <div className="details">
          {rule.check && (
            <p>
              <b>Что проверить:</b> {rule.check}
            </p>
          )}
          <p>
            <b>Основание:</b> {src.doc}
            {src.clause ? `, ${src.clause}` : ''}
          </p>
          {src.checklist && (
            <p>
              <b>Проверочный лист:</b> {src.checklist}
            </p>
          )}
          <p>
            Актуально на {src.as_of}
            {!src.verified && ' · реквизиты требуют сверки с текстом НПА'}
          </p>
        </div>
      )}

      {rule.status === 'violation' && (
        <div className="viol-extra">
          <textarea
            placeholder="Комментарий: что именно не так"
            value={comment}
            disabled={disabled}
            onChange={(e) => setComment(e.target.value)}
            onBlur={saveComment}
          />
          <div className="photo-row">
            {rule.photo_url && <img src={rule.photo_url} alt="" />}
            {!disabled && (
              <button type="button" className="photo-btn" onClick={pick} disabled={busy}>
                {rule.photo_url ? 'Заменить фото' : 'Сфотографировать «как есть»'}
              </button>
            )}
            <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onFile} />
          </div>
        </div>
      )}
      {rule.status === 'ok' && rule.evidence === 'photo' && !disabled && (
        <div className="viol-extra">
          <div className="photo-row">
            {rule.photo_url && <img src={rule.photo_url} alt="" />}
            <button type="button" className="photo-btn" onClick={pick} disabled={busy}>
              {rule.photo_url ? 'Заменить фото' : 'Приложить фото-подтверждение'}
            </button>
            <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onFile} />
          </div>
        </div>
      )}
      {err && <div className="err">{err}</div>}
    </div>
  )
}
