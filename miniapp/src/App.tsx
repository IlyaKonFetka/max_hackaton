import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Spinner } from '@maxhub/max-ui'
import { api, ApiError, type ApplicableRule, type FinishResult, type SessionPayload, type Status } from './api'
import { haptic, insideMax, startParam, webApp } from './bridge'
import { RuleCard } from './components/RuleCard'
import { NotApplicable } from './components/NotApplicable'
import { Tasks } from './components/Tasks'

type Tab = 'check' | 'na' | 'tasks'
type Screen =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; noVenue?: boolean }
  | { kind: 'check'; session: SessionPayload }
  | { kind: 'finished'; session: SessionPayload; result: FinishResult }

function parseSessionParam(): number | undefined {
  const sp = startParam()
  const m = /^s(\d+)$/.exec(sp)
  return m ? Number(m[1]) : undefined
}

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: 'loading' })
  const [tab, setTab] = useState<Tab>('check')
  const [toast, setToast] = useState<string | null>(null)
  const [finishing, setFinishing] = useState(false)

  const showToast = useCallback((t: string) => {
    setToast(t)
    window.setTimeout(() => setToast(null), 1800)
  }, [])

  const load = useCallback(async () => {
    setScreen({ kind: 'loading' })
    try {
      const s = await api.session(parseSessionParam())
      setScreen({ kind: 'check', session: s })
    } catch (e) {
      const err = e as ApiError
      if (err.status === 404) setScreen({ kind: 'error', message: err.message, noVenue: true })
      else if (err.status === 401) setScreen({ kind: 'error', message: 'Откройте приложение из чата с ботом — нужны данные авторизации MAX.' })
      else setScreen({ kind: 'error', message: err.message || 'Не удалось связаться с сервером' })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Обновление одного пункта в состоянии без перезагрузки списка.
  const patchRule = (session: SessionPayload, ruleId: string, patch: Partial<ApplicableRule>, progress?: SessionPayload['progress']) => {
    const applicable = session.applicable.map((r) => (r.id === ruleId ? { ...r, ...patch } : r))
    const next: SessionPayload = { ...session, applicable, progress: progress ?? recount(applicable) }
    setScreen({ kind: 'check', session: next })
  }

  const recount = (rules: ApplicableRule[]): SessionPayload['progress'] => {
    const c = { ok: 0, violation: 0, unknown: 0 }
    for (const r of rules) if (r.status) c[r.status] += 1
    const answered = c.ok + c.violation + c.unknown
    return { total: rules.length, answered, remaining: rules.length - answered, ...c }
  }

  const finish = async (session: SessionPayload) => {
    if (session.progress.remaining > 0) {
      const ok = window.confirm(`Не отмечено ${session.progress.remaining} пунктов. Они попадут в акт как «не проверено». Завершить?`)
      if (!ok) return
    }
    setFinishing(true)
    try {
      const result = await api.finish(session.id)
      haptic('success')
      setScreen({ kind: 'finished', session: { ...session, finished_at: new Date().toISOString() }, result })
      webApp()?.disableClosingConfirmation?.()
    } catch (e) {
      showToast((e as Error).message)
      haptic('error')
    } finally {
      setFinishing(false)
    }
  }

  const startNew = async () => {
    setScreen({ kind: 'loading' })
    try {
      const s = await api.newSession()
      setTab('check')
      setScreen({ kind: 'check', session: s })
    } catch (e) {
      setScreen({ kind: 'error', message: (e as Error).message })
    }
  }

  if (screen.kind === 'loading') {
    return (
      <div className="center">
        <Spinner />
        <p>Загружаю применимые требования…</p>
      </div>
    )
  }

  if (screen.kind === 'error') {
    return (
      <div className="center">
        <h2 className="title">{screen.noVenue ? 'Сначала профиль' : 'Не получилось'}</h2>
        <p>
          {screen.noVenue
            ? 'Профиль заведения ещё не заполнен. Вернитесь в чат с ботом и ответьте на 7 вопросов — после этого здесь появится ваш список требований.'
            : screen.message}
        </p>
        {!insideMax() && <p className="err">Приложение открыто вне MAX — данных авторизации нет.</p>}
        <Button onClick={() => void load()}>Повторить</Button>
      </div>
    )
  }

  if (screen.kind === 'finished') {
    return <Finished session={screen.session} result={screen.result} onNew={startNew} onTasks={() => { setTab('tasks'); setScreen({ kind: 'check', session: screen.session }) }} />
  }

  const { session } = screen
  const readOnly = Boolean(session.finished_at)
  return (
    <CheckScreen
      session={session}
      readOnly={readOnly}
      tab={tab}
      setTab={setTab}
      finishing={finishing}
      onFinish={() => finish(session)}
      onNew={startNew}
      onStatus={async (rule, status) => {
        const r = await api.answer(session.id, rule.id, status)
        patchRule(session, rule.id, { status: r.status, comment: r.comment, photo_url: r.photo_url }, r.progress)
      }}
      onComment={async (rule, comment) => {
        const r = await api.answer(session.id, rule.id, (rule.status ?? 'violation') as Status, comment)
        patchRule(session, rule.id, { comment: r.comment }, r.progress)
        showToast('Комментарий сохранён')
      }}
      onPhoto={async (rule, file) => {
        const r = await api.photo(session.id, rule.id, file)
        patchRule(session, rule.id, { status: r.status, photo_url: r.photo_url })
        showToast('Фото прикреплено')
      }}
      toast={toast}
    />
  )
}

interface CheckProps {
  session: SessionPayload
  readOnly: boolean
  tab: Tab
  setTab: (t: Tab) => void
  finishing: boolean
  onFinish: () => void
  onNew: () => void
  onStatus: (rule: ApplicableRule, status: Status) => Promise<void>
  onComment: (rule: ApplicableRule, comment: string) => Promise<void>
  onPhoto: (rule: ApplicableRule, file: File) => Promise<void>
  toast: string | null
}

function CheckScreen({ session, readOnly, tab, setTab, finishing, onFinish, onNew, onStatus, onComment, onPhoto, toast }: CheckProps) {
  const p = session.progress
  const pct = p.total ? Math.round((p.answered / p.total) * 100) : 0

  const grouped = useMemo(() => {
    const m = new Map<string, ApplicableRule[]>()
    for (const r of session.applicable) m.set(r.agency_label, [...(m.get(r.agency_label) ?? []), r])
    return [...m.entries()]
  }, [session.applicable])

  useEffect(() => {
    // Пока есть неотмеченные пункты — просим подтвердить закрытие окна.
    const wa = webApp()
    if (!wa) return
    if (!readOnly && p.answered > 0 && p.remaining > 0) wa.enableClosingConfirmation?.()
    else wa.disableClosingConfirmation?.()
  }, [p.answered, p.remaining, readOnly])

  return (
    <div className="page">
      <div className="topbar">
        <h1 className="title">{session.venue.name}</h1>
        <p className="subtitle">
          {readOnly ? 'Самопроверка завершена · ' : ''}
          {p.answered} из {p.total} · соблюдается {p.ok} · нарушений {p.violation} · не знаю {p.unknown}
        </p>
        <div className="progress"><i style={{ width: `${pct}%` }} /></div>
        <div className="tabs">
          <button className={`tab ${tab === 'check' ? 'active' : ''}`} onClick={() => setTab('check')}>
            Применимо <span className="n">{session.applicable.length}</span>
          </button>
          <button className={`tab ${tab === 'na' ? 'active' : ''}`} onClick={() => setTab('na')}>
            Не применимо <span className="n">{session.not_applicable.length}</span>
          </button>
          <button className={`tab ${tab === 'tasks' ? 'active' : ''}`} onClick={() => setTab('tasks')}>
            Задачи
          </button>
        </div>
      </div>

      {tab === 'check' &&
        grouped.map(([agency, rules]) => (
          <div className="group" key={agency}>
            <h3>{agency} · {rules.length}</h3>
            {rules.map((r) => (
              <RuleCard
                key={r.id}
                rule={r}
                disabled={readOnly}
                onStatus={(s) => onStatus(r, s)}
                onComment={(c) => onComment(r, c)}
                onPhoto={(f) => onPhoto(r, f)}
              />
            ))}
          </div>
        ))}
      {tab === 'na' && <NotApplicable rules={session.not_applicable} />}
      {tab === 'tasks' && <Tasks />}

      {tab === 'check' && (
        <div className="bottom">
          <div className="bottom-inner">
            {readOnly ? (
              <Button size="large" stretched onClick={onNew}>Новая самопроверка</Button>
            ) : (
              <Button size="large" stretched loading={finishing} disabled={p.answered === 0 || finishing} onClick={onFinish}>
                {p.remaining === 0 ? 'Завершить и сформировать акт' : `Завершить (${p.remaining} не отмечено)`}
              </Button>
            )}
          </div>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

function Finished({ session, result, onNew, onTasks }: { session: SessionPayload; result: FinishResult; onNew: () => void; onTasks: () => void }) {
  const c = result.counts
  return (
    <div className="page">
      <div className="topbar">
        <h1 className="title">Самопроверка завершена</h1>
        <p className="subtitle">{session.venue.name} · справочник {session.rules_version}</p>
      </div>
      <div className="stats">
        <div className="stat ok"><b>{c.ok}</b><span>соблюдается</span></div>
        <div className="stat violation"><b>{c.violation}</b><span>нарушений</span></div>
        <div className="stat unknown"><b>{c.unknown}</b><span>не знаю</span></div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <p className="card-title">{result.act_sent_to_chat ? 'Акт отправлен в чат с ботом' : 'Акт сформирован'}</p>
        <p className="details" style={{ marginTop: 4 }}>
          PDF с профилем объекта, перечнем требований со ссылками на НПА, статусами и фото. Его можно переслать бухгалтеру или показать инспектору на профилактическом визите.
        </p>
      </div>
      {result.tasks.length > 0 ? (
        <div className="card">
          <p className="card-title">План устранения — {result.tasks.length} {plural(result.tasks.length, 'задача', 'задачи', 'задач')}</p>
          <ul className="reasons">
            {result.tasks.map((t) => (
              <li key={t.id}>до {new Date(t.due_date).toLocaleDateString('ru-RU')} — {t.title}</li>
            ))}
          </ul>
          <p className="details" style={{ marginTop: 6 }}>Бот напомнит о сроках. Назначить ответственного — в чате командой /tasks.</p>
        </div>
      ) : (
        <div className="card"><p className="card-title">Нарушений нет — план устранения не нужен.</p></div>
      )}
      <div className="bottom">
        <div className="bottom-inner">
          {result.tasks.length > 0 && <Button size="large" variant="secondary" onClick={onTasks}>Задачи</Button>}
          <Button size="large" onClick={onNew}>Новая самопроверка</Button>
        </div>
      </div>
    </div>
  )
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}
