import type { Bundle, Check, Offer } from '../api/types'
import { CHECK_STYLE, KIND_LABEL, PRICE_UNIT, localParts, rub, when } from '../lib/format'
import Icon, { KIND_ICON } from './Icon'

export function SyntheticBadge() {
  return (
    <span
      className="rounded-md bg-[#f0f1ff] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-guru-from"
      title="Цена и условия сгенерированы для демо — это не реальные данные поставщика"
    >
      демо-данные
    </span>
  )
}

/** У старых Offer нет title — собираем подпись из variant (тип номера, тариф, класс) */
export function offerTitle(o: Offer): string {
  if (o.title) return o.title
  const v = Object.values(o.variant ?? {}).filter((x) => typeof x === 'string').join(', ')
  return `${KIND_LABEL[o.kind]} ${o.item_id}${v ? ` · ${v}` : ''}`
}

const KIND_TINT: Record<Offer['kind'], string> = {
  flight: 'bg-brand-light text-brand',
  train: 'bg-brand-light text-brand',
  transfer: 'bg-canvas text-ink-grey',
  hotel: 'bg-lime-soft text-[#2f6b0f]',
}

export function OfferRow({ o, last, markSynthetic = true, checks = [] }: { o: Offer; last?: boolean; markSynthetic?: boolean; checks?: Check[] }) {
  const start = o.times && localParts(o.times.start)
  const end = o.times && localParts(o.times.end)
  return (
    <li className="relative flex gap-3 pb-4">
      {!last && <span className="absolute bottom-0 left-[17px] top-10 w-px bg-black/10" aria-hidden />}
      <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${KIND_TINT[o.kind]}`}>
        <Icon name={KIND_ICON[o.kind]} className="h-[18px] w-[18px]" />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 text-[14px] font-semibold leading-snug">
              {offerTitle(o)}
              {markSynthetic && o.synthetic && <SyntheticBadge />}
            </div>
            {start && end && (
              <div className="mt-0.5 text-[13px] text-ink-grey">
                <span className="font-medium text-ink">{start.time}</span> {start.date} →{' '}
                <span className="font-medium text-ink">{end.time}</span> {end.date === start.date ? '' : end.date}
                <span className="ml-1 text-[11px] text-ink-muted">UTC{start.tz}</span>
              </div>
            )}
          </div>
          <div className="price shrink-0 text-[15px]">{rub(o.total_rub)}</div>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-muted">
          <span>
            {rub(o.unit_price_rub)} {PRICE_UNIT[o.price_unit]}
            {o.units > 1 && ` × ${o.units}`}
            {o.quantity > 1 && ` × ${o.quantity} шт.`}
          </span>
          {o.included?.length ? <span>{o.included.join(', ')}</span> : null}
          {o.baggage ? <span>багаж {o.baggage}</span> : null}
          {o.cancel_policy && (
            <span className={o.cancel_policy.free_until ? 'text-ok' : ''}>
              {o.cancel_policy.free_until ? `бесплатная отмена до ${when(o.cancel_policy.free_until)}` : 'невозвратный'}
            </span>
          )}
        </div>
        {checks.length > 0 && (
          <div className="mt-2 rounded-xl border border-black/[.05] bg-white p-2.5">
            <Checks checks={checks} />
          </div>
        )}
      </div>
    </li>
  )
}

/** Проверка относится к последней из своих позиций в таймлайне: «прибытие vs заселение» — к отелю, «обратная дорога» — к рейсу назад */
export function attachChecks(b: Bundle): { byOffer: Map<string, Check[]>; general: Check[] } {
  const order = new Map(b.items.map((o, i) => [o.offer_id, i]))
  const byOffer = new Map<string, Check[]>()
  const general: Check[] = []
  for (const c of b.checks) {
    const idx = (c.offer_ids ?? []).map((id) => order.get(id)).filter((i): i is number => i != null)
    if (!idx.length) {
      general.push(c)
      continue
    }
    const id = b.items[Math.max(...idx)].offer_id
    byOffer.set(id, [...(byOffer.get(id) ?? []), c])
  }
  return { byOffer, general }
}

export function Checks({ checks }: { checks: Check[] }) {
  if (!checks.length) return null
  return (
    <ul className="space-y-1.5">
      {checks.map((c, i) => {
        const st = CHECK_STYLE[c.status]
        return (
          <li key={i} className="flex gap-2.5 text-[13px]">
            <span className={`mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full ${st.cls}`}>
              <Icon name={st.icon} className="h-3 w-3" strokeWidth={2.6} />
            </span>
            <div className="min-w-0">
              <div className={c.status === 'ok' ? 'text-ink-grey' : 'font-medium text-ink'}>{c.text}</div>
              {c.options?.length ? (
                <ul className="mt-1 space-y-0.5">
                  {c.options.map((o) => (
                    <li key={o} className="text-xs text-ink-grey before:mr-1.5 before:text-brand before:content-['→']">
                      {o}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

interface Props {
  bundle: Bundle
  selected?: boolean
  onCheckout: () => void
  checkoutBusy: boolean
  /** агент занят (пересборка, оформление другого комплекта) — кнопка недоступна */
  disabled?: boolean
}

export default function BundleCard({ bundle, selected, onCheckout, checkoutBusy, disabled }: Props) {
  const { byOffer, general } = attachChecks(bundle)
  const blocked = bundle.checks.some((c) => c.status === 'block')
  const warns = bundle.checks.filter((c) => c.status === 'warn').length
  const allSynthetic = bundle.items.every((o) => o.synthetic)
  return (
    <article className={`card flex flex-col overflow-hidden ${selected ? 'ring-2 ring-brand' : ''}`}>
      <header className="flex items-start justify-between gap-3 p-4 pb-3">
        <div>
          <span className="rounded-full bg-brand-light px-2.5 py-1 text-xs font-bold uppercase tracking-wide text-brand">{bundle.label}</span>
          {selected && <span className="ml-1.5 rounded-full bg-brand px-2 py-0.5 align-middle text-[10px] font-bold text-white">ваш выбор</span>}
          {allSynthetic && (
            <span className="ml-1.5 align-middle">
              <SyntheticBadge />
            </span>
          )}
          <div className="mt-2 text-xs text-ink-muted">
            {blocked ? 'есть блокирующая проблема' : warns ? `${warns} ${warns === 1 ? 'момент' : 'момента'} на заметку` : 'все стыковки проверены'}
          </div>
        </div>
        <div className="text-right">
          <div className="price text-[26px] leading-none">{rub(bundle.total_rub)}</div>
          <div className="mt-1 text-[11px] text-ink-muted">за всех и весь период</div>
        </div>
      </header>

      <ol className="px-4 pt-1">
        {bundle.items.map((o, i) => (
          <OfferRow key={o.offer_id + i} o={o} last={i === bundle.items.length - 1} markSynthetic={!allSynthetic} checks={byOffer.get(o.offer_id)} />
        ))}
      </ol>

      <div className="mt-auto space-y-3 border-t border-black/[.05] bg-canvas/50 p-4">
        <Checks checks={general} />
        {!!bundle.advantages?.length && (
          <div className="text-[13px]">
            <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Почему подходит</div>
            {bundle.advantages.map((claim, i) => (
              <div key={i} className="text-ink-grey" title={claim.refs.join(', ')}>{claim.text}</div>
            ))}
          </div>
        )}
        {(bundle.tradeoffs.length > 0 || bundle.risks.length > 0) && (
          <div className={`grid gap-3 text-[13px] ${bundle.tradeoffs.length && bundle.risks.length ? 'sm:grid-cols-2' : ''}`}>
            {bundle.tradeoffs.length > 0 && (
              <div>
                <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Чем жертвуем</div>
                {bundle.tradeoffs.map((t, i) => (
                  <div key={i} className="text-ink-grey" title={t.refs.join(', ')}>{t.text}</div>
                ))}
              </div>
            )}
            {bundle.risks.length > 0 && (
              <div>
                <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Риски</div>
                {bundle.risks.map((t, i) => (
                  <div key={i} className="text-ink-grey" title={t.refs.join(', ')}>{t.text}</div>
                ))}
              </div>
            )}
          </div>
        )}
        <button
          onClick={onCheckout}
          disabled={blocked || checkoutBusy || disabled}
          className="btn-primary w-full py-3 text-[15px]"
          title={blocked ? 'Сначала исправьте блокирующие проблемы' : undefined}
        >
          {blocked ? 'Есть блокирующие проблемы' : checkoutBusy ? 'Проверяю план…' : `Оформить за ${rub(bundle.total_rub)}`}
        </button>
      </div>
    </article>
  )
}
