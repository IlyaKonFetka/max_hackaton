import { useEffect, useRef, useState } from 'react'
import { Button, Spinner } from '@maxhub/max-ui'
import { api, type Task } from '../api'
import { haptic } from '../bridge'
import { Photo } from './Photo'

function fmt(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function Tasks() {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const pending = useRef<number | null>(null)

  const load = () => api.tasks().then((r) => setTasks(r.tasks)).catch((e) => setErr((e as Error).message))
  useEffect(() => {
    load()
  }, [])

  const done = async (t: Task, file?: File) => {
    setBusyId(t.id)
    try {
      await api.taskDone(t.id, file)
      haptic('success')
      await load()
    } catch (e) {
      setErr((e as Error).message)
      haptic('error')
    } finally {
      setBusyId(null)
    }
  }

  const withPhoto = (t: Task) => {
    pending.current = t.id
    fileRef.current?.click()
  }
  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    const id = pending.current
    pending.current = null
    const t = tasks?.find((x) => x.id === id)
    if (f && t) void done(t, f)
  }

  if (err) return <div className="center"><p className="err">{err}</p></div>
  if (!tasks) return <div className="center"><Spinner /></div>
  if (tasks.length === 0) return <div className="center"><p>Открытых задач нет.</p></div>

  const now = Date.now()
  return (
    <>
      <p className="subtitle" style={{ margin: '10px 4px 0' }}>
        Каждое нарушение — задача со сроком из справочника. Назначить ответственного можно в боте: /tasks.
      </p>
      <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onFile} />
      {tasks.map((t) => {
        const overdue = new Date(t.due_date).getTime() < now
        return (
          <div className={`card ${overdue ? 'violation' : ''}`} key={t.id}>
            <p className="card-title">{t.title}</p>
            <div className="meta">
              <span className={`badge ${overdue ? 'sev-high' : ''}`}>{overdue ? 'просрочено · ' : 'до '}{fmt(t.due_date)}</span>
              {t.assignee_name && <span>Ответственный: {t.assignee_name}</span>}
            </div>
            <div className="photo-row" style={{ marginTop: 10 }}>
              {t.photo_before_url && <Photo src={t.photo_before_url} alt="как есть" />}
              <Button size="small" variant="secondary" disabled={busyId === t.id} onClick={() => withPhoto(t)}>
                Выполнено, с фото
              </Button>
              <Button size="small" variant="ghost" disabled={busyId === t.id} onClick={() => done(t)}>
                Без фото
              </Button>
            </div>
          </div>
        )
      })}
    </>
  )
}
