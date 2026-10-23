import type { Bundle, Requirement } from '../api/types'
import { compareBundles } from '../lib/compare'
import { rub } from '../lib/format'
import Icon from './Icon'

interface Props {
  bundles: Bundle[]
  requirements: Requirement[]
  selectedId: string | null
  busy: boolean
  onSelect: (b: Bundle) => void
}

/** Сравнение готовых комплектов: колонки — комплекты, строки — критерии этого пользователя */
export default function CompareTable({ bundles, requirements, selectedId, busy, onSelect }: Props) {
  const rows = compareBundles(bundles, requirements)
  const wins = bundles.map((_, i) => rows.filter((r) => r.best.length === 1 && r.best[0] === i).length)

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between gap-3 border-b border-black/[.05] p-4">
        <div>
          <h2 className="text-[17px] font-bold">Сравнение комплектов</h2>
          <p className="text-xs text-ink-muted">по тому, что важно вам — критерии из ваших требований сверху</p>
        </div>
        <span className="hidden items-center gap-1.5 text-xs text-ink-muted sm:flex">
          <span className="h-2.5 w-2.5 rounded-full bg-lime" /> лучшее значение
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] border-collapse text-sm">
          <thead>
            <tr>
              <th className="w-[200px] p-0" />
              {bundles.map((b, i) => {
                const selected = b.id === selectedId
                return (
                  <th key={b.id} className={`p-3 text-left align-top ${selected ? 'bg-brand-light/60' : ''}`}>
                    <div className="flex items-center gap-1.5">
                      <span className="rounded-full bg-brand-light px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-brand">{b.label}</span>
                      {selected && <span className="rounded-full bg-brand px-2 py-0.5 text-[10px] font-bold text-white">ваш выбор</span>}
                    </div>
                    <div className="price mt-2 text-xl">{rub(b.total_rub)}</div>
                    {wins[i] > 0 && (
                      <div className="text-[11px] font-medium text-[#2f6b0f]">
                        лучше по {wins[i]} {wins[i] === 1 ? 'критерию' : 'критериям'}
                      </div>
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-black/[.05]">
                <th className="p-3 text-left align-top font-normal">
                  <div className="font-semibold text-ink">{r.label}</div>
                  {r.why && <div className="text-[11px] text-brand">{r.why}</div>}
                </th>
                {r.cells.map((c, i) => {
                  const best = r.best.includes(i)
                  return (
                    <td key={i} className={`p-3 align-top ${bundles[i].id === selectedId ? 'bg-brand-light/60' : ''}`}>
                      <span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 ${best ? 'bg-lime-soft font-semibold text-[#2f6b0f]' : 'text-ink'}`}>
                        {best && <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.6} />}
                        {c.text}
                      </span>
                      {c.note && <div className="mt-0.5 px-2 text-[11px] text-ink-muted">{c.note}</div>}
                    </td>
                  )
                })}
              </tr>
            ))}
            <tr className="border-t border-black/[.05]">
              <td />
              {bundles.map((b) => {
                const selected = b.id === selectedId
                const blocked = b.checks.some((c) => c.status === 'block')
                return (
                  <td key={b.id} className={`p-3 ${selected ? 'bg-brand-light/60' : ''}`}>
                    <button
                      onClick={() => onSelect(b)}
                      disabled={busy || selected || blocked}
                      className={selected ? 'btn-soft w-full' : 'btn-primary w-full'}
                    >
                      {selected ? 'Выбран' : blocked ? 'Есть блок' : 'Выбрать'}
                    </button>
                  </td>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  )
}
