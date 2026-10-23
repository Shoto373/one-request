import type { DestinationOption, ProposedChange, Requirement, Stage, TripState } from '../api/types'
import BundleCard from './BundleCard'
import { Destinations, NoOptions } from './Destinations'
import Icon from './Icon'
import Requirements from './Requirements'

const STAGES: { key: Stage; label: string }[] = [
  { key: 'clarify', label: 'Уточнение' },
  { key: 'inspire', label: 'Направление' },
  { key: 'select', label: 'Подбор' },
  { key: 'compare', label: 'Сравнение' },
  { key: 'assemble', label: 'Сборка' },
  { key: 'checkout', label: 'Оформление' },
  { key: 'booked', label: 'Готово' },
]

function Stepper({ stage }: { stage?: Stage }) {
  const idx = Math.max(0, STAGES.findIndex((s) => s.key === stage))
  return (
    <ol className="flex items-center gap-1 overflow-x-auto pb-1 text-[12px] font-medium">
      {STAGES.map((s, i) => (
        <li key={s.key} className="flex shrink-0 items-center gap-1">
          <span
            className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 ${
              i === idx ? 'bg-brand text-white shadow-lift' : i < idx ? 'bg-brand-light text-brand' : 'bg-white text-ink-muted'
            }`}
          >
            {i < idx ? <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.6} /> : <span className="tabular-nums">{i + 1}</span>}
            {s.label}
          </span>
          {i < STAGES.length - 1 && <span className={`h-px w-3 ${i < idx ? 'bg-brand/40' : 'bg-black/10'}`} />}
        </li>
      ))}
    </ol>
  )
}

interface Props {
  state: TripState | null
  busy: boolean
  checkoutBusyId: string | null
  onEditRequirement: (r: Requirement, text: string) => void
  onRemoveRequirement: (r: Requirement) => void
  onConfirmRequirement: (r: Requirement) => void
  onSelectDestination: (d: DestinationOption) => void
  onApplyChange: (r: ProposedChange) => void
  onCheckout: (bundleId: string) => void
}

export default function TripCanvas(p: Props) {
  const s = p.state
  const noOptions = !!s?.proposed_changes?.length
  const empty = !s?.requirements.length && !s?.bundles.length && !s?.destinations.length && !noOptions

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-page space-y-5 p-4 md:p-6">
        <Stepper stage={s?.stage} />

        {empty ? (
          <EmptyCanvas />
        ) : (
          <>
            <Requirements
              requirements={s?.requirements ?? []}
              busy={p.busy}
              onEdit={p.onEditRequirement}
              onRemove={p.onRemoveRequirement}
              onConfirm={p.onConfirmRequirement}
            />

            {noOptions && (
              <NoOptions changes={s?.proposed_changes ?? []} busy={p.busy} onApply={p.onApplyChange} />
            )}

            {!!s?.destinations.length && <Destinations items={s.destinations} busy={p.busy} onSelect={p.onSelectDestination} />}

            {!!s?.bundles.length && (
              <section>
                <div className="mb-3 flex items-baseline justify-between gap-3">
                  <h2 className="text-[17px] font-bold">Комплекты поездки</h2>
                  <span className="hidden text-xs text-ink-muted sm:inline">дорога туда и обратно, трансферы и отель — проверены вместе</span>
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  {s.bundles.map((b, i) => (
                    <BundleCard key={b.id} bundle={b} best={i === 0 && s.bundles.length > 1} checkoutBusy={p.checkoutBusyId === b.id} onCheckout={() => p.onCheckout(b.id)} />
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function EmptyCanvas() {
  const steps = [
    { icon: 'sparkle' as const, title: 'Поймёт запрос', text: 'Отделит ваши требования от своих догадок и спросит только главное' },
    { icon: 'sun' as const, title: 'Предложит, куда', text: 'Погода, цена поездки целиком, время в пути и виза — по каждому месту' },
    { icon: 'shield' as const, title: 'Проверит стыковки', text: 'Прилёт, трансфер и заселение, обратная дорога — до оплаты' },
    { icon: 'wallet' as const, title: 'Соберёт в один заказ', text: 'Перелёт или поезд, трансферы и отель — одной оплатой' },
  ]
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {steps.map((s, i) => (
        <div key={s.title} className="card flex gap-3 p-4">
          <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${i === 0 ? 'guru-gradient text-white' : 'bg-brand-light text-brand'}`}>
            <Icon name={s.icon} className="h-5 w-5" />
          </span>
          <div>
            <div className="font-semibold">{s.title}</div>
            <div className="text-sm text-ink-grey">{s.text}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
