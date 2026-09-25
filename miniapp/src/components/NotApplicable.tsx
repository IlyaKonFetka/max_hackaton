import type { NotApplicableRule } from '../api'

export function NotApplicable({ rules }: { rules: NotApplicableRule[] }) {
  if (rules.length === 0) {
    return (
      <div className="center">
        <p>Все требования справочника применимы к вашему заведению.</p>
      </div>
    )
  }
  // Группируем по причине: «Кухня: нет кухни» → список требований.
  const groups = new Map<string, NotApplicableRule[]>()
  for (const r of rules) {
    const key = r.reasons.join('; ') || 'по условиям профиля'
    groups.set(key, [...(groups.get(key) ?? []), r])
  }
  return (
    <>
      <p className="subtitle" style={{ margin: '10px 4px 0' }}>
        Эти пункты есть в проверочных листах, но к вашему профилю не относятся. Если заведение изменится, заполните профиль в боте заново (/profile), и список пересчитается.
      </p>
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
    </>
  )
}
