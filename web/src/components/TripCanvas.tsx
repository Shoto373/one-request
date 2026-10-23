import type { Requirement, Stage, TripState } from '../api/types'
import { REQ_KIND } from '../lib/format'
import BundleCard from './BundleCard'

const STAGES: { key: Stage; label: string }[] = [
  { key: 'clarify', label: 'Уточнение' },
  { key: 'inspire', label: 'Направление' },
  { key: 'select', label: 'Подбор' },
  { key: 'compare', label: 'Сравнение' },
  { key: 'assemble', label: 'Сборка' },
  { key: 'checkout', label: 'Оформление' },
  { key: 'booked', label: 'Куплено' },
]

function reqText(r: Requirement): string {
  if (r.label) return r.label
  return `${r.key}: ${typeof r.value === 'object' ? JSON.stringify(r.value) : String(r.value)}`
}

interface Props {
  state: TripState | null
  busy: boolean
  checkoutBusyId: string | null
  onRemoveRequirement: (key: string) => void
  onCheckout: (bundleId: string) => void
}

export default function TripCanvas({ state, busy, checkoutBusyId, onRemoveRequirement, onCheckout }: Props) {
  const stageIdx = STAGES.findIndex((s) => s.key === state?.stage)

  return (
    <div className="h-full space-y-5 overflow-y-auto p-5">
      <ol className="flex flex-wrap gap-1 text-[11px]">
        {STAGES.map((s, i) => (
          <li
            key={s.key}
            className={`rounded-full px-2.5 py-1 ${
              i === stageIdx ? 'bg-brand text-white' : i < stageIdx ? 'bg-brand-light text-brand' : 'bg-slate-100 text-slate-400'
            }`}
          >
            {s.label}
          </li>
        ))}
      </ol>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Что агент понял</h2>
        {!state?.requirements.length ? (
          <p className="text-sm text-slate-400">Пока ничего — напишите, какую поездку хотите.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {state.requirements.map((r) => {
              const k = REQ_KIND[r.kind]
              return (
                <span
                  key={r.key}
                  className={`group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${k.cls}`}
                  title={`${k.label}. ${k.hint}${r.quote ? `\nИз вашего сообщения: «${r.quote}»` : ''}`}
                >
                  {reqText(r)}
                  {r.kind !== 'hard' && (
                    <button
                      onClick={() => onRemoveRequirement(r.key)}
                      disabled={busy}
                      className="opacity-50 hover:opacity-100 disabled:opacity-20"
                      aria-label="Убрать"
                      title="Убрать — агент перепроверит план"
                    >
                      ×
                    </button>
                  )}
                </span>
              )
            })}
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-500">
          {(['hard', 'soft', 'assumption'] as const).map((k) => (
            <span key={k} className="inline-flex items-center gap-1">
              <span className={`inline-block h-2.5 w-2.5 rounded-full border ${REQ_KIND[k].cls}`} />
              {REQ_KIND[k].label}
            </span>
          ))}
        </div>
      </section>

      {state?.open_questions.length ? (
        <section className="rounded-lg border border-brand/30 bg-brand-light p-3 text-sm">
          <div className="mb-1 font-medium text-brand-dark">Агенту нужно уточнить</div>
          {state.open_questions.map((q) => (
            <div key={q.key}>
              {q.question} <span className="text-xs text-slate-500">— {q.why}</span>
            </div>
          ))}
        </section>
      ) : null}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">Комплекты поездки</h2>
        {!state?.bundles.length ? (
          <p className="text-sm text-slate-400">Здесь появятся проверенные комплекты: дорога туда и обратно, трансферы и отель.</p>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {state.bundles.map((b) => (
              <BundleCard
                key={b.id}
                bundle={b}
                checkoutBusy={checkoutBusyId === b.id}
                onCheckout={() => onCheckout(b.id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
