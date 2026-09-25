import { useState } from 'react'
import type { ChecklistStatus, Funnel, NotApplicableRule } from '../api'

interface Props {
  rules: NotApplicableRule[]
  funnel?: Funnel
  checklists?: ChecklistStatus[]
}

export function NotApplicable({ rules, funnel, checklists = [] }: Props) {
  const own = rules.filter((r) => !r.other_domain)
  // Требования других отраслей одной строкой: кафе незачем читать, почему к нему не относятся правила для магазинов.
  const other = new Map<string, number>()
  for (const r of rules) if (r.other_domain) other.set(r.other_domain, (other.get(r.other_domain) ?? 0) + 1)

  // Группируем по причине: «Кухня: нет кухни» → список требований.
  const groups = new Map<string, NotApplicableRule[]>()
  for (const r of own) {
    const key = r.reasons.join('; ') || 'по условиям профиля'
    groups.set(key, [...(groups.get(key) ?? []), r])
  }

  return (
    <>
      {funnel && funnel.checklists_total > 0 && <FunnelCard funnel={funnel} checklists={checklists} />}

      {own.length === 0 ? (
        <p className="subtitle" style={{ margin: '14px 4px 0' }}>По вашему профилю отсеивать нечего: все требования справочника для вашего вида деятельности применимы.</p>
      ) : (
        <p className="subtitle" style={{ margin: '14px 4px 0' }}>
          Эти пункты есть в проверочных листах, но к вашему профилю не относятся. Если заведение изменится, заполните профиль в боте заново (/profile), и список пересчитается.
        </p>
      )}
      {[...groups.entries()].map(([reason, items]) => (
        <div className="group" key={reason}>
          <h3>Потому что: {reason}</h3>
          {items.map((r) => (
            <div className="card" key={r.id}>
              <p className="card-title">{r.title}</p>
              <div className="meta">
                <span className="badge">{r.agency_label}</span>
                <span>{r.source.doc}{r.source.clause ? `, ${r.source.clause}` : ''}</span>
              </div>
            </div>
          ))}
        </div>
      ))}
      {other.size > 0 && (
        <p className="subtitle" style={{ margin: '14px 4px 0' }}>
          Ещё в справочнике есть требования для других видов деятельности: {[...other.entries()].map(([d, n]) => `${d.toLowerCase()} — ${n}`).join(', ')}. Их здесь не показываем.
        </p>
      )}
    </>
  )
}

function FunnelCard({ funnel, checklists }: { funnel: Funnel; checklists: ChecklistStatus[] }) {
  const [open, setOpen] = useState(false)
  const mine = checklists.filter((c) => c.status === 'yes' || c.status === 'maybe')
  return (
    <div className="card funnel">
      <p className="card-title">
        Ваши проверочные листы: {funnel.checklists_yes} из {funnel.checklists_total}
      </p>
      <p className="details">
        Столько форм проверочных листов у Роспотребнадзора, Роструда и МЧС. Остальные — для других отраслей и объектов
        {funnel.checklists_maybe > 0 ? `; ещё ${funnel.checklists_maybe} относятся при условиях, которых нет в профиле` : ''}.
      </p>
      <ul className="funnel-rows">
        {funnel.agencies.map((a) => (
          <li key={a.agency}>
            <span>{a.label}</span>
            <b>{a.yes}</b>
            <span className="muted">из {a.total}{a.maybe ? ` · ещё ${a.maybe} при условии` : ''}</span>
          </li>
        ))}
      </ul>
      {funnel.detailed.map((d) => (
        <p className="details" key={d.id}>
          Лист «{d.title}» разобран полностью: к вам относятся <b>{d.applicable}</b> вопросов из {d.questions}, {d.not_applicable} не относятся по профилю, {d.other_domain} — другие виды деятельности ({d.other_scopes.map((s) => s.toLowerCase()).join(', ')}).
        </p>
      ))}
      <button className="linkbtn" onClick={() => setOpen(!open)}>
        {open ? 'Скрыть список листов' : `Показать ваши листы (${mine.length})`}
      </button>
      {open && (
        <ul className="lists">
          {mine.map((c) => (
            <li key={c.id}>
              <span className={`badge ${c.status === 'maybe' ? 'maybe' : ''}`}>{agencyShort(c.agency)} {c.number}</span>
              {c.title}
              {c.status === 'maybe' && <span className="muted"> — {c.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function agencyShort(a: string): string {
  return a === 'rospotrebnadzor' ? 'РПН' : a === 'rostrud' ? 'Роструд' : a === 'mchs' ? 'МЧС' : a
}
