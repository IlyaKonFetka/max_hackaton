/** Клиент API. Авторизация: initData из MAX Bridge в заголовке X-Max-Init-Data, бэкенд сверяет подпись. */

import { apiUrl, initData } from './bridge'

export type Status = 'ok' | 'violation' | 'unknown'

export interface Source {
  doc: string
  clause: string
  checklist: string
  url: string
  as_of: string
  verified: boolean
}

export interface Rule {
  id: string
  agency: string
  agency_label: string
  title: string
  check: string
  source: Source
  period: string
  period_label: string
  evidence: 'check' | 'photo' | 'document'
  severity: 'high' | 'medium' | 'low'
  fix_days: number
}

export interface ApplicableRule extends Rule {
  status: Status | null
  comment: string
  photo_url: string | null
}

export interface NotApplicableRule extends Rule {
  reasons: string[]
}

export interface Progress {
  total: number
  answered: number
  remaining: number
  ok: number
  violation: number
  unknown: number
}

export interface SessionPayload {
  id: number
  started_at: string
  finished_at: string | null
  rules_version: string
  venue: { id: number; name: string; region: string; profile_lines: string[] }
  progress: Progress
  applicable: ApplicableRule[]
  not_applicable: NotApplicableRule[]
  agencies: { key: string; label: string }[]
}

export interface Task {
  id: number
  rule_id: string
  title: string
  status: 'open' | 'done'
  due_date: string
  assignee_name: string
  photo_before_url: string | null
  photo_after_url: string | null
}

export interface FinishResult {
  id: number
  counts: { ok: number; violation: number; unknown: number; unanswered: number }
  tasks: Task[]
  act_sent_to_chat: boolean
}

export interface Me {
  user: { id: number; first_name: string }
  venue: { id: number; name: string; region: string; profile_lines: string[] } | null
  session: { id: number; finished: boolean; progress: Progress } | null
  open_tasks: number
  start_param?: string
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const d = initData()
  if (d) h['X-Max-Init-Data'] = d
  // Локальная отладка без MAX: ?debug_user=<id> и DEBUG_AUTH=1 на бэкенде.
  const dbg = new URLSearchParams(location.search).get('debug_user')
  if (!d && dbg) h['X-Debug-User'] = dbg
  return h
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${apiUrl()}${path}`, { ...init, headers: headers((init.headers as Record<string, string>) ?? {}) })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
    } catch {
      /* пустое тело */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const api = {
  me: () => request<Me>('/api/me'),
  session: (id?: number) => request<SessionPayload>(`/api/session${id ? `?id=${id}` : ''}`),
  newSession: () => request<SessionPayload>('/api/session/new', { method: 'POST' }),
  answer: (sessionId: number, ruleId: string, status: Status, comment?: string) =>
    request<{ rule_id: string; status: Status; comment: string; photo_url: string | null; progress: Progress }>(
      `/api/session/${sessionId}/answers/${ruleId}`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, comment }) },
    ),
  photo: (sessionId: number, ruleId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file, file.name || 'photo.jpg')
    return request<{ rule_id: string; status: Status; photo_url: string }>(
      `/api/session/${sessionId}/answers/${ruleId}/photo`,
      { method: 'POST', body: fd },
    )
  },
  finish: (sessionId: number) => request<FinishResult>(`/api/session/${sessionId}/finish`, { method: 'POST' }),
  tasks: () => request<{ tasks: Task[] }>('/api/tasks'),
  taskDone: (taskId: number, file?: File) => {
    const fd = new FormData()
    if (file) fd.append('file', file, file.name || 'photo.jpg')
    return request<Task>(`/api/tasks/${taskId}/done`, { method: 'POST', body: fd })
  },
}
