import type { Bundle, Check, Offer } from '../api/types'
import { CHECK_STYLE, KIND_LABEL, PRICE_UNIT, localParts, rub, when } from '../lib/format'
import Icon, { KIND_ICON } from './Icon'

function providerTerms(o: Offer): string[] {
  const v = o.variant ?? {}
  const terms = (v.terms && typeof v.terms === 'object' ? v.terms : v) as Record<string, unknown>
  const facts: string[] = []
  if (terms.breakfast_included === true) facts.push('завтрак включён')
  if (terms.free_cancellation === true) facts.push('бесплатная отмена')
  if (terms.cancellation_status === true) facts.push('возвратный билет')
  if (terms.pet_allowed === true) facts.push('с питомцем разрешено')
  if (terms.check_in_time) facts.push(`заезд с ${terms.check_in_time}`)
  if (terms.check_out_time) facts.push(`выезд до ${terms.check_out_time}`)
  if (o.kind !== 'hotel' && terms.baggage && typeof terms.baggage === 'object') {
    const baggage = terms.baggage as Record<string, unknown>
    const details = Object.entries(baggage).map(([key, value]) => `${key}: ${String(value)}`).join(', ')
    if (details) facts.push(`багаж: ${details}`)
  }
  return facts
}

export function SyntheticBadge() {
  return (
    <span
      className="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700 border border-blue-200"
      title="Демо-данные"
    >
      демо
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
  transfer: 'bg-amber-100 text-amber-900',
  hotel: 'bg-lime-soft text-[#2f6b0f]',
}

export function OfferRow({ o, last, markSynthetic = true, checks = [] }: { o: Offer; last?: boolean; markSynthetic?: boolean; checks?: Check[] }) {
  const start = o.times && localParts(o.times.start)
  const end = o.times && localParts(o.times.end)
  const legs = Array.isArray(o.variant?.legs) ? o.variant.legs as Array<Record<string, unknown>> : []
  const termsList = providerTerms(o)

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
              <span>{offerTitle(o)}</span>
              {markSynthetic && o.synthetic && <SyntheticBadge />}
              {o.provider === 'tutu' && (
                <span className="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700 border border-blue-200">
                  Туту {o.kind === 'flight' ? 'Авиа' : 'ЖД'}
                </span>
              )}
              {o.provider === 'ostrovok' && (
                <span className="rounded-md bg-orange-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-orange-700 border border-orange-200">
                  {o.kind === 'transfer' ? 'Островок Трансфер' : `Островок ${o.variant?.rating ? `★ ${o.variant.rating}` : ''}`}
                </span>
              )}
              {o.provider === 'yandex_go' && (
                <span className="rounded-md bg-yellow-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-yellow-900 border border-yellow-300">
                  Яндекс Go
                </span>
              )}
              {o.source_url && (
                <a
                  href={o.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded bg-black/[0.04] hover:bg-black/[0.08] px-2 py-0.5 text-[11px] text-brand font-medium transition"
                  title="Открыть прямое предложение у партнера"
                >
                  <span>
                    {o.provider === 'ostrovok' || o.kind === 'hotel'
                      ? (o.kind === 'transfer' ? 'Островок Трансфер' : 'Островок')
                      : o.kind === 'transfer'
                        ? 'Островок Трансфер'
                        : o.provider === 'yandex_go'
                          ? 'Яндекс Go'
                          : 'Туту'}
                  </span>
                  <span className="text-[10px]">↗</span>
                </a>
              )}
            </div>
            {legs.length > 0 ? (
              <div className="mt-1 space-y-0.5 text-[12px] text-ink-grey">
                {legs.map((leg, index) => (
                  <div key={index} className="flex flex-wrap items-center gap-1">
                    <span className="font-medium text-ink">
                      {typeof leg.label === 'string' ? (leg.label === 'return' ? 'Обратно: ' : 'Туда: ') : ''}
                      {String(leg.from ?? '')} → {String(leg.to ?? '')}
                    </span>
                    {typeof leg.departure_at === 'string' && typeof leg.arrival_at === 'string' && (
                      <span className="text-ink-muted">
                        · {new Date(leg.departure_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} —{' '}
                        {new Date(leg.arrival_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} (МСК)
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ) : start && end ? (
              <div className="mt-1 text-[13px] text-ink-grey">
                <span className="font-medium text-ink">{start.time}</span> {start.date} →{' '}
                <span className="font-medium text-ink">{end.time}</span> {end.date === start.date ? '' : end.date}
                <span className="ml-1 text-[11px] text-ink-muted">UTC{start.tz}</span>
              </div>
            ) : null}
          </div>
          <div className="price shrink-0 text-[15px] font-bold text-ink">{rub(o.total_rub)}</div>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted">
          <span className="rounded bg-black/[0.03] px-1.5 py-0.5 font-medium text-ink">
            {rub(o.unit_price_rub)} {PRICE_UNIT[o.price_unit]}
            {o.units > 1 && ` × ${o.units}`}
            {o.quantity > 1 && ` × ${o.quantity} шт.`}
          </span>
          {o.included?.length ? (
            <span className="rounded bg-black/[0.03] px-1.5 py-0.5 text-ink-grey">
              {o.included.join(', ')}
            </span>
          ) : null}
          {o.baggage ? (
            <span className="rounded bg-black/[0.03] px-1.5 py-0.5 text-ink-grey">
              багаж {o.baggage}
            </span>
          ) : null}
          {o.cancel_policy && (
            <span className={`rounded px-1.5 py-0.5 font-medium ${o.cancel_policy.free_until ? 'bg-emerald-50 text-emerald-700' : 'bg-black/[0.03] text-ink-muted'}`}>
              {o.cancel_policy.free_until ? `бесплатная отмена до ${when(o.cancel_policy.free_until)}` : 'невозвратный'}
            </span>
          )}
          {termsList.map((fact) => (
            <span key={fact} className="rounded bg-black/[0.03] px-1.5 py-0.5 text-ink-grey">
              {fact}
            </span>
          ))}
          {o.source_url && (
            <a
              className="text-brand font-medium hover:underline ml-auto"
              href={o.source_url}
              target="_blank"
              rel="noreferrer"
            >
              {o.provider === 'ostrovok' || o.kind === 'hotel'
                ? (o.kind === 'transfer' ? 'трансфер на Островке' : 'перейти на Островок')
                : o.kind === 'transfer'
                  ? 'трансфер на Островке'
                  : o.provider === 'yandex_go'
                    ? 'маршрут в Яндекс Go'
                    : 'билеты на Туту'}
            </a>
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

/** Проверка относится к последней из своих позиций в таймлайне */
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
  disabled?: boolean
}

export default function BundleCard({ bundle, selected, onCheckout, checkoutBusy, disabled }: Props) {
  const { byOffer, general } = attachChecks(bundle)
  const blocked = bundle.checks.some((c) => c.status === 'block')
  const warns = bundle.checks.filter((c) => c.status === 'warn').length
  const allSynthetic = bundle.items.every((o) => o.synthetic)

  // Filter out internal technical MCP debug check lines from user-facing advantages and risks
  const cleanAdvantages = (bundle.advantages ?? []).filter(
    (c) => !c.text.toLowerCase().includes('проверка tutu mcp') && !c.text.includes('«availability»') && !c.text.includes('«transfer_fit»')
  )
  const cleanTradeoffs = (bundle.tradeoffs ?? []).filter(
    (c) => !c.text.toLowerCase().includes('проверка tutu mcp')
  )
  const cleanRisks = (bundle.risks ?? []).filter(
    (c) => !c.text.toLowerCase().includes('проверка tutu mcp') && !c.text.includes('«price_completeness»')
  )

  return (
    <article className={`card flex flex-col overflow-hidden transition-shadow hover:shadow-card-hover ${selected ? 'ring-2 ring-brand' : ''}`}>
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
          <div className="price text-[26px] font-bold leading-none">{rub(bundle.total_rub)}</div>
          <div className="mt-1 text-[11px] text-ink-muted">за всех и весь период</div>
          {bundle.price_note && <div className="mt-1 max-w-[240px] text-[11px] text-ink-muted">{bundle.price_note}</div>}
        </div>
      </header>

      <ol className="px-4 pt-1">
        {bundle.items.map((o, i) => (
          <OfferRow key={o.offer_id + i} o={o} last={i === bundle.items.length - 1} markSynthetic={!allSynthetic} checks={byOffer.get(o.offer_id)} />
        ))}
      </ol>

      <div className="mt-auto space-y-3 border-t border-black/[.05] bg-canvas/50 p-4">
        <Checks checks={general} />
        {cleanAdvantages.length > 0 && (
          <div className="text-[13px]">
            <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Плюсы варианта</div>
            {cleanAdvantages.map((claim, i) => (
              <div key={i} className="text-ink-grey">{claim.text}</div>
            ))}
          </div>
        )}
        {(cleanTradeoffs.length > 0 || cleanRisks.length > 0) && (
          <div className={`grid gap-3 text-[13px] ${cleanTradeoffs.length && cleanRisks.length ? 'sm:grid-cols-2' : ''}`}>
            {cleanTradeoffs.length > 0 && (
              <div>
                <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Особенности</div>
                {cleanTradeoffs.map((claim, i) => (
                  <div key={i} className="text-ink-grey">{claim.text}</div>
                ))}
              </div>
            )}
            {cleanRisks.length > 0 && (
              <div>
                <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-bad">На что обратить внимание</div>
                {cleanRisks.map((claim, i) => (
                  <div key={i} className="text-ink-grey">{claim.text}</div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="pt-2">
          <button
            onClick={onCheckout}
            disabled={disabled || blocked || checkoutBusy}
            className="w-full rounded-2xl bg-brand py-3 text-center text-sm font-semibold text-white transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-ink-line disabled:text-ink-muted shadow-sm"
          >
            {checkoutBusy ? 'Оформляем…' : selected ? `Оформить за ${rub(bundle.total_rub)}` : `Выбрать комплект (${rub(bundle.total_rub)})`}
          </button>
        </div>
      </div>
    </article>
  )
}
