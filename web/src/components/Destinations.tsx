import type { Claim, DestinationOption, Relaxation } from '../api/types'
import { MONTH_NAME, VISA_LABEL, duration, rub } from '../lib/format'
import Icon from './Icon'

/** Обложка без фото: градиент по характеру места (море / горы / город) */
function cover(d: DestinationOption): string {
  const tags = (d.tags ?? []).join(' ')
  if (/гор/.test(tags) && !/мор/.test(tags)) return 'from-[#0f5de4] via-[#2d4cf9] to-[#53af24]'
  if (/город|культур/.test(tags)) return 'from-[#4225f2] to-[#0460ea]'
  return 'from-[#0a4fec] via-[#0f5de4] to-[#04cafd]'
}

function ClaimList({ items, kind }: { items?: Claim[]; kind: 'why' | 'risk' }) {
  if (!items?.length) return null
  return (
    <ul className="space-y-1.5">
      {items.map((c, i) => (
        <li key={i} className="flex gap-2 text-[13px] leading-snug" title={c.refs.length ? `Источник: ${c.refs.join(', ')}` : undefined}>
          <span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full ${kind === 'why' ? 'bg-lime-soft text-[#2f6b0f]' : 'bg-warn-bg text-warn'}`}>
            <Icon name={kind === 'why' ? 'check' : 'alert'} className="h-3 w-3" strokeWidth={2.6} />
          </span>
          <span className={kind === 'why' ? 'text-ink' : 'text-ink-grey'}>{c.text}</span>
        </li>
      ))}
    </ul>
  )
}

function DestinationCard({ d, best, busy, onSelect }: { d: DestinationOption; best: boolean; busy: boolean; onSelect: () => void }) {
  const visa = d.visa ? VISA_LABEL[d.visa.regime] : null
  return (
    <article className={`card group flex flex-col overflow-hidden transition hover:-translate-y-0.5 hover:shadow-lift ${best ? 'ring-2 ring-brand' : ''}`}>
      <div className={`relative h-32 bg-gradient-to-br ${cover(d)} p-4 text-white`}>
        <div className="absolute -right-2 top-6 h-16 w-16 rounded-full bg-lime/30 blur-xl" aria-hidden />
        <svg viewBox="0 0 400 60" preserveAspectRatio="none" className="absolute inset-x-0 bottom-0 h-10 w-full text-white/15" aria-hidden>
          <path d="M0 30 Q 50 10 100 30 T 200 30 T 300 30 T 400 30 V60 H0z" fill="currentColor" />
          <path d="M0 42 Q 50 24 100 42 T 200 42 T 300 42 T 400 42 V60 H0z" fill="currentColor" />
        </svg>
        {(d.climate?.sea_temp ?? d.climate?.air_temp_day) != null && (
          <div className="absolute bottom-3 right-4 text-right leading-none">
            <div className="text-[40px] font-bold tracking-tight">{Math.round((d.climate?.sea_temp ?? d.climate?.air_temp_day)!)}°</div>
            <div className="mt-1 text-[11px] font-medium text-white/80">{d.climate?.sea_temp != null ? 'вода' : 'воздух'}</div>
          </div>
        )}
        {best && (
          <span className="absolute right-3 top-3 rounded-full bg-lime px-2.5 py-1 text-[11px] font-bold text-ink">Рекомендуем</span>
        )}
        <div className="relative flex h-full flex-col justify-end">
          <div className="text-[22px] font-bold leading-tight tracking-tight">{d.name}</div>
          {d.country && <div className="text-sm text-white/80">{d.country}</div>}
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex flex-wrap gap-1.5 text-[12px] font-medium">
          {d.climate?.air_temp_day != null && (
            <span className="chip border-transparent bg-canvas text-ink">
              <Icon name="sun" className="h-3.5 w-3.5 text-warn" /> {Math.round(d.climate.air_temp_day)}°
            </span>
          )}
          {d.climate?.sea_temp != null && (
            <span className="chip border-transparent bg-brand-light text-brand">
              <Icon name="wave" className="h-3.5 w-3.5" /> море {Math.round(d.climate.sea_temp)}°
            </span>
          )}
          {d.travel && (
            <span className="chip border-transparent bg-canvas text-ink">
              <Icon name={d.travel.kind === 'train' ? 'train' : 'plane'} className="h-3.5 w-3.5" /> {duration(d.travel.minutes)}
              {d.travel.direct ? ', прямой' : ''}
            </span>
          )}
          {visa && (
            <span className={`chip border-transparent ${visa.cls}`} title={d.visa?.note}>
              <Icon name="passport" className="h-3.5 w-3.5" /> {visa.text}
            </span>
          )}
        </div>

        {d.climate?.month && (
          <div className="-mt-1 text-[11px] text-ink-muted">погода — средняя в {MONTH_NAME[d.climate.month - 1]}</div>
        )}

        <ClaimList items={d.why} kind="why" />
        <ClaimList items={d.risks} kind="risk" />

        <div className="mt-auto flex items-end justify-between gap-3 border-t border-black/[.05] pt-3">
          <div>
            {d.est_total_rub != null && (
              <>
                <div className="text-[11px] text-ink-muted">поездка целиком, примерно</div>
                <div className="price text-xl">≈ {rub(d.est_total_rub)}</div>
              </>
            )}
          </div>
          <button onClick={onSelect} disabled={busy || d.feasible === false} className="btn-primary">
            Выбрать
          </button>
        </div>
      </div>
    </article>
  )
}

export function Destinations({ items, busy, onSelect }: { items: DestinationOption[]; busy: boolean; onSelect: (d: DestinationOption) => void }) {
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-[17px] font-bold">Куда можно поехать</h2>
        <span className="hidden text-xs text-ink-muted sm:inline">только направления, где поездка точно собирается</span>
      </div>
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {items.map((d, i) => (
          <DestinationCard key={d.id} d={d} best={i === 0} busy={busy} onSelect={() => onSelect(d)} />
        ))}
      </div>
    </section>
  )
}

export function NoOptions({ reasons, relaxations, busy, onApply }: { reasons: string[]; relaxations: Relaxation[]; busy: boolean; onApply: (r: Relaxation) => void }) {
  return (
    <section className="card overflow-hidden">
      <div className="flex gap-3 border-b border-black/[.05] bg-bad-bg/60 p-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white text-bad">
          <Icon name="alert" className="h-5 w-5" />
        </span>
        <div>
          <h2 className="text-[17px] font-bold">Под все условия вариантов нет</h2>
          {reasons.map((r, i) => (
            <p key={i} className="mt-0.5 text-sm text-ink-grey">{r}</p>
          ))}
          <p className="mt-1 text-xs text-ink-muted">Ваши обязательные условия агент сам не ослабляет — выберите изменение, если оно подходит.</p>
        </div>
      </div>
      {relaxations.length > 0 && (
        <div className="grid gap-3 p-4 sm:grid-cols-2">
          {relaxations.map((r, i) => (
            <button
              key={r.id}
              onClick={() => onApply(r)}
              disabled={busy}
              className="group flex items-center gap-3 rounded-2xl border border-black/[.08] p-3.5 text-left transition hover:border-brand hover:shadow-lift disabled:opacity-50"
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-light text-sm font-bold text-brand">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink">{r.text}</span>
                {r.est_total_rub != null && <span className="text-xs text-ink-muted">итого ≈ {rub(r.est_total_rub)}</span>}
              </span>
              <Icon name="chevron" className="h-4 w-4 text-ink-muted group-hover:text-brand" />
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
