import type { Offer } from '../api/types'
import type { ItemChange, PlanDiff } from '../lib/diff'
import { localParts, rub } from '../lib/format'
import { offerTitle } from './BundleCard'
import Icon, { KIND_ICON } from './Icon'

function when(o?: Offer): string {
  if (!o?.times) return ''
  const s = localParts(o.times.start)
  return `${s.date}, ${s.time}`
}

function Delta({ value }: { value: number }) {
  if (!value) return <span className="text-ink-muted">цена та же</span>
  return (
    <span className={`font-semibold ${value > 0 ? 'text-bad' : 'text-ok'}`}>
      {value > 0 ? '+' : '−'}
      {rub(Math.abs(value))}
    </span>
  )
}

/** «Пересобрал план»: что сохранено, что заменено и почему, на сколько изменилась цена */
export default function ReplanDiff({ diff, onClose }: { diff: PlanDiff; onClose?: () => void }) {
  const delta = diff.priceAfter - diff.priceBefore
  return (
    <section className="card overflow-hidden ring-1 ring-brand/20">
      <header className="flex items-start gap-3 border-b border-black/[.05] bg-brand-light/50 p-4">
        <span className="guru-gradient grid h-9 w-9 shrink-0 place-items-center rounded-full text-white">
          <Icon name="refresh" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[17px] font-bold">Агент пересобрал план</h2>
          <p className="text-sm text-ink-grey">
            {diff.entry?.change ? `${diff.entry.change}. ` : ''}
            Перепроверил все зависимые части, заменил только то, что перестало подходить.
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] text-ink-muted">итого</div>
          <div className="text-sm">
            <span className="text-ink-muted line-through">{rub(diff.priceBefore)}</span>{' '}
            <span className="price text-lg">{rub(diff.priceAfter)}</span>
          </div>
          <div className="text-xs">
            <Delta value={delta} />
          </div>
        </div>
        {onClose && (
          <button onClick={onClose} className="rounded-full p-1 text-ink-muted hover:bg-black/5" aria-label="Скрыть">
            <Icon name="x" className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="grid gap-4 p-4 md:grid-cols-[1fr_auto]">
        <div className="space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Заменено</div>
          {[
            ...diff.replaced,
            ...diff.added.map((a): ItemChange => ({ kind: a.kind, after: a })),
            ...diff.removed.map((r): ItemChange => ({ kind: r.kind, before: r })),
          ].map((c, i) => (
            <div key={i} className="flex items-center gap-3 rounded-xl bg-canvas p-2.5 text-[13px]">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand">
                <Icon name={KIND_ICON[c.kind]} className="h-4 w-4" />
              </span>
              <div className="grid min-w-0 flex-1 items-center gap-x-2 gap-y-0.5 sm:grid-cols-[1fr_auto_1fr]">
                <div className="min-w-0 text-ink-muted">
                  {c.before ? (
                    <>
                      <div className="truncate line-through">{offerTitle(c.before)}</div>
                      <div className="text-[11px]">{when(c.before)}</div>
                    </>
                  ) : (
                    <span>— не было</span>
                  )}
                </div>
                <Icon name="chevron" className="hidden h-4 w-4 text-brand sm:block" />
                <div className="min-w-0">
                  {c.after ? (
                    <>
                      <div className="truncate font-semibold text-ink">{offerTitle(c.after)}</div>
                      <div className="text-[11px] text-ink-grey">{when(c.after)}</div>
                    </>
                  ) : (
                    <span className="text-ink-muted">убрано</span>
                  )}
                </div>
              </div>
              <div className="shrink-0 text-xs">
                <Delta value={(c.after?.total_rub ?? 0) - (c.before?.total_rub ?? 0)} />
              </div>
            </div>
          ))}
          {diff.entry?.replaced?.length ? (
            <ul className="space-y-0.5 pt-1 text-xs text-ink-grey">
              {diff.entry.replaced.map((t, i) => (
                <li key={i} className="before:mr-1.5 before:text-brand before:content-['→']">{t}</li>
              ))}
            </ul>
          ) : null}
        </div>

        {diff.kept.length > 0 && (
          <div className="md:w-[240px]">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Сохранено — подходит</div>
            <ul className="space-y-1.5">
              {diff.kept.map((o) => (
                <li key={o.offer_id} className="flex items-center gap-2 text-[13px]">
                  <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-lime-soft text-[#2f6b0f]">
                    <Icon name="check" className="h-3 w-3" strokeWidth={2.6} />
                  </span>
                  <span className="truncate">{offerTitle(o)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
