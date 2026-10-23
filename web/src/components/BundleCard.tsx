import type { Bundle, Check, Offer } from '../api/types'
import { CHECK_STYLE, KIND_ICON, PRICE_UNIT, localParts, rub, when } from '../lib/format'

export function SyntheticBadge() {
  return (
    <span
      className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-violet-700"
      title="Цена и условия сгенерированы для демо — это не реальные данные поставщика"
    >
      сгенерировано
    </span>
  )
}

const KIND_LABEL: Record<Offer['kind'], string> = { flight: 'Перелёт', train: 'Поезд', transfer: 'Трансфер', hotel: 'Отель' }

/** У Offer из бэкенда нет title — собираем подпись из variant (тип номера, тариф, класс) */
export function offerTitle(o: Offer): string {
  if (o.title) return o.title
  const v = Object.values(o.variant ?? {}).filter(Boolean).join(', ')
  return `${KIND_LABEL[o.kind]} ${o.item_id}${v ? ` · ${v}` : ''}`
}

export function OfferRow({ o }: { o: Offer }) {
  const start = o.times && localParts(o.times.start)
  const end = o.times && localParts(o.times.end)
  return (
    <li className="flex gap-3 py-2">
      <div className="w-6 shrink-0 pt-0.5 text-center">{KIND_ICON[o.kind]}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
          {offerTitle(o)}
          {o.synthetic && <SyntheticBadge />}
        </div>
        {start && end && (
          <div className="text-xs text-slate-500">
            {start.date} {start.time} → {end.date === start.date ? '' : end.date + ' '}
            {end.time} <span className="text-slate-400">({start.tz})</span>
          </div>
        )}
        <div className="text-xs text-slate-500">
          {rub(o.unit_price_rub)} {PRICE_UNIT[o.price_unit]}
          {o.units > 1 && <> × {o.units}</>}
          {o.quantity > 1 && <> × {o.quantity} шт.</>}
          {o.included?.length ? <> · {o.included.join(', ')}</> : null}
          {o.baggage ? <> · багаж {o.baggage}</> : null}
        </div>
        {o.cancel_policy && (
          <div className="text-xs text-slate-500">
            {o.cancel_policy.free_until
              ? `Бесплатная отмена до ${when(o.cancel_policy.free_until)}`
              : `Невозвратный: штраф ${rub(o.cancel_policy.penalty_rub)}`}
          </div>
        )}
      </div>
      <div className="shrink-0 text-right text-sm font-medium">{rub(o.total_rub)}</div>
    </li>
  )
}

export function Checks({ checks }: { checks: Check[] }) {
  if (!checks.length) return null
  return (
    <ul className="space-y-1.5">
      {checks.map((c, i) => {
        const st = CHECK_STYLE[c.status]
        return (
          <li key={i} className={`rounded-md border px-2.5 py-1.5 text-xs ${st.cls}`}>
            <span className="mr-1.5 font-bold">{st.icon}</span>
            {c.text}
            {c.options?.length ? (
              <ul className="mt-1 list-disc pl-5 text-slate-700">
                {c.options.map((o) => (
                  <li key={o}>{o}</li>
                ))}
              </ul>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

interface Props {
  bundle: Bundle
  onCheckout: () => void
  checkoutBusy: boolean
}

export default function BundleCard({ bundle, onCheckout, checkoutBusy }: Props) {
  const blocked = bundle.checks.some((c) => c.status === 'block')
  return (
    <article className="rounded-xl border bg-white shadow-sm">
      <header className="flex items-baseline justify-between gap-3 border-b px-4 py-3">
        <h3 className="font-semibold capitalize">{bundle.label}</h3>
        <div className="text-right">
          <div className="text-lg font-semibold">{rub(bundle.total_rub)}</div>
          <div className="text-[11px] text-slate-500">итого за всех и весь период</div>
        </div>
      </header>

      <ul className="divide-y px-4">
        {bundle.items.map((o, i) => (
          <OfferRow key={o.offer_id + i} o={o} />
        ))}
      </ul>

      <div className="space-y-3 border-t px-4 py-3">
        <Checks checks={bundle.checks} />
        {bundle.tradeoffs.length > 0 && (
          <div className="text-xs">
            <div className="font-medium text-slate-700">Чем жертвуем</div>
            {bundle.tradeoffs.map((t, i) => (
              <div key={i} className="text-slate-600" title={t.refs.join(', ')}>
                — {t.text}
              </div>
            ))}
          </div>
        )}
        {bundle.risks.length > 0 && (
          <div className="text-xs">
            <div className="font-medium text-slate-700">Риски</div>
            {bundle.risks.map((t, i) => (
              <div key={i} className="text-slate-600" title={t.refs.join(', ')}>
                — {t.text}
              </div>
            ))}
          </div>
        )}
        <button
          onClick={onCheckout}
          disabled={blocked || checkoutBusy}
          className="w-full rounded-lg bg-brand py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:cursor-not-allowed disabled:opacity-40"
          title={blocked ? 'Сначала исправьте блокирующие проблемы' : undefined}
        >
          {blocked ? 'Есть блокирующие проблемы' : checkoutBusy ? 'Проверяю план…' : 'Оформить этот комплект'}
        </button>
      </div>
    </article>
  )
}
