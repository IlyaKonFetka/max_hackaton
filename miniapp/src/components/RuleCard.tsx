import { useRef, useState } from 'react'
import { api, type ApplicableRule, type Status } from '../api'
import { haptic } from '../bridge'

interface Props {
  rule: ApplicableRule
  disabled?: boolean
  canAsk?: boolean
  onStatus: (status: Status) => Promise<void>
  onComment: (comment: string) => Promise<void>
  onPhoto: (file: File) => Promise<void>
}

const STATUS_LABEL: Record<Status, string> = { ok: 'Соблюдается', violation: 'Нарушение', unknown: 'Не знаю' }

export function RuleCard({ rule, disabled, canAsk, onStatus, onComment, onPhoto }: Props) {
  const [open, setOpen] = useState(false)
  const [asking, setAsking] = useState(false)
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
  const needsPhoto = rule.status === 'violation' || (rule.status === 'ok' && rule.evidence === 'photo')
  return (
    <div className={`card ${rule.status ?? ''}`}>
      <p className="card-title">{rule.title}</p>
      <div className="meta">
        <span className="badge">{rule.period_label}</span>
        {rule.severity === 'high' && <span className="badge sev-high">важное</span>}
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

      <div className="card-actions">
        <button type="button" className="linkbtn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? 'Свернуть' : 'Подробнее'}
        </button>
        {canAsk && (
          <button
            type="button"
            className={`askbtn ${asking ? 'on' : ''}`}
            onClick={() => setAsking((v) => !v)}
            aria-expanded={asking}
            aria-label="Спросить помощника про этот пункт"
          >
            <BotIcon /> Спросить
          </button>
        )}
      </div>

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

      {canAsk && asking && <AskBox ruleId={rule.id} />}

      {rule.status === 'violation' && (
        <textarea
          className="comment"
          placeholder="Что не так? Можно не заполнять"
          value={comment}
          disabled={disabled}
          onChange={(e) => setComment(e.target.value)}
          onBlur={saveComment}
        />
      )}
      {needsPhoto && (rule.photo_url || !disabled) && (
        <div className="photo-row">
          {rule.photo_url && <img src={rule.photo_url} alt="" />}
          {!disabled && (
            <button type="button" className="photo-btn" onClick={pick} disabled={busy}>
              {rule.photo_url ? 'Заменить фото' : rule.status === 'violation' ? 'Фото «как есть»' : 'Добавить фото'}
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onFile} />
        </div>
      )}
      {err && <div className="err">{err}</div>}
    </div>
  )
}

const QUICK = ['Как это выполнить?', 'Какие документы нужны?']

/** Вопрос помощнику про этот пункт. Отвечает по справочнику заведения, применимость не меняет. */
function AskBox({ ruleId }: { ruleId: string }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [chat, setChat] = useState<{ q: string; a: string; note: string }[]>([])
  const [err, setErr] = useState<string | null>(null)

  const send = async (text: string) => {
    const question = text.trim()
    if (question.length < 2 || busy) return
    setBusy(true)
    setErr(null)
    try {
      const r = await api.ask(question, ruleId)
      setChat((c) => [...c, { q: question, a: r.answer, note: r.disclaimer }])
      setQ('')
      haptic('success')
    } catch (e) {
      setErr((e as Error).message)
      haptic('error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ask">
      {chat.map((m, i) => (
        <div key={i} className="ask-msg">
          <p className="ask-q">{m.q}</p>
          <p className="ask-a">{m.a}</p>
          {i === chat.length - 1 && <p className="muted">{m.note}</p>}
        </div>
      ))}
      {chat.length === 0 && (
        <div className="ask-quick">
          {QUICK.map((t) => (
            <button key={t} type="button" disabled={busy} onClick={() => send(t)}>
              {t}
            </button>
          ))}
        </div>
      )}
      <div className="ask-input">
        <textarea
          rows={1}
          placeholder={busy ? 'Помощник отвечает…' : 'Ваш вопрос про этот пункт'}
          value={q}
          disabled={busy}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send(q)
            }
          }}
        />
        <button type="button" className="sendbtn" disabled={busy || q.trim().length < 2} onClick={() => send(q)} aria-label="Отправить">
          {busy ? <span className="dots" aria-hidden>…</span> : <PlaneIcon />}
        </button>
      </div>
      {err && <p className="err">{err}</p>}
    </div>
  )
}

/** Робот тонкой линией: знак помощника вместо эмодзи. */
function BotIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 3.5v3" />
      <rect x="4.5" y="6.5" width="15" height="12" rx="3.5" />
      <path d="M2.5 11v3M21.5 11v3" />
      <circle cx="9.5" cy="12.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="12.5" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  )
}

function PlaneIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
      <path fill="currentColor" d="M3.4 20.4 21.9 12 3.4 3.6 3.4 10.1 16.6 12 3.4 13.9z" />
    </svg>
  )
}
