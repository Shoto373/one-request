import { useState } from 'react'
import type { TraceStep } from '../api/types'
import Icon from './Icon'

/** Человеческие подписи шагов агента; неизвестные показываем по имени инструмента */
export const STEP_LABEL: Record<string, string> = {
  parse_request: 'Разбираю запрос',
  parse_request_rules: 'Разбираю запрос правилами (без LLM)',
  parse_answer: 'Разбираю ответ',
  next_questions: 'Решаю, что уточнить',
  get_climate: 'Смотрю погоду',
  suggest_destinations: 'Подбираю направления',
  search_transport: 'Ищу перелёты и поезда',
  search_flights: 'Ищу перелёты',
  search_trains: 'Ищу поезда',
  search_hotels: 'Ищу отели',
  search_transfers: 'Ищу трансферы',
  build_bundles: 'Собираю комплекты',
  validate_plan: 'Проверяю стыковки',
  explain_no_bundles: 'Выясняю, почему нет вариантов',
  suggest_destinations_result: 'Получил направления',
  build_bundles_result: 'Получил комплекты',
  explain_destination: 'Объясняю выбор по данным',
  stage_transition: 'Перехожу к следующему этапу',
  state_diff: 'Обновляю план',
  replan: 'Пересобираю план',
  edit_requirement: 'Меняю требование',
  remove_requirement: 'Убираю требование',
  remove_assumption: 'Убираю догадку',
  select_destination: 'Фиксирую направление',
  accept_alternative: 'Применяю изменение',
  select_bundle: 'Фиксирую комплект',
}

const TYPE_STYLE: Record<string, { label: string; cls: string }> = {
  llm: { label: 'LLM', cls: 'guru-gradient text-white' },
  tool: { label: 'инструмент', cls: 'bg-brand-light text-brand' },
  state: { label: 'состояние', cls: 'bg-lime-soft text-[#2f6b0f]' },
}

function Json({ value }: { value: unknown }) {
  if (value == null) return <span className="text-ink-muted">—</span>
  return (
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-2 font-mono text-[11px] leading-relaxed text-ink-grey">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function StepRow({ s, n }: { s: TraceStep; n: number }) {
  const [open, setOpen] = useState(false)
  const t = TYPE_STYLE[s.step_type] ?? { label: s.step_type, cls: 'bg-canvas text-ink-grey' }
  // 'calling' — вызов начат (результат идёт следующим шагом), это не ошибка
  const failed = s.status !== 'ok' && s.status !== 'calling'
  return (
    <li className="relative pl-7">
      <span
        className={`absolute left-0 top-2.5 grid h-5 w-5 place-items-center rounded-full text-[10px] font-bold ${
          failed ? 'bg-bad text-white' : 'bg-white text-ink-grey ring-1 ring-black/10'
        }`}
      >
        {n}
      </span>
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 rounded-xl px-2 py-2 text-left hover:bg-canvas">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">{STEP_LABEL[s.name] ?? s.name}</span>
          <span className="font-mono text-[11px] text-ink-muted">{s.name}</span>
        </span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${t.cls}`}>{t.label}</span>
        <Icon name="chevron" className={`h-4 w-4 text-ink-muted transition ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="mb-2 ml-2 space-y-2 text-xs">
          <div>
            <div className="mb-1 font-semibold text-ink-grey">Вход</div>
            <Json value={s.input} />
          </div>
          <div>
            <div className="mb-1 font-semibold text-ink-grey">Результат</div>
            <Json value={s.output} />
          </div>
        </div>
      )}
    </li>
  )
}

interface Props {
  trace: TraceStep[]
  onClose: () => void
}

/** «Что делает агент»: реальные вызовы инструментов и LLM с входами/выходами, не пересказ */
export default function TracePanel({ trace, onClose }: Props) {
  // группируем по версии плана (ход агента); живые шаги без версии — текущий ход
  const groups: { version: number | null; steps: TraceStep[] }[] = []
  for (const s of trace) {
    const v = s.version ?? null
    const last = groups[groups.length - 1]
    if (last && last.version === v) last.steps.push(s)
    else groups.push({ version: v, steps: [s] })
  }

  return (
    <aside className="flex h-full flex-col bg-white">
      <header className="flex items-center justify-between border-b border-black/[.06] px-4 py-3">
        <div>
          <div className="font-semibold">Что делает агент</div>
          <div className="text-xs text-ink-muted">реальные вызовы инструментов, по ходам</div>
        </div>
        <button onClick={onClose} className="btn-ghost h-9 w-9 !p-0" aria-label="Закрыть">
          <Icon name="x" className="h-5 w-5" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {!trace.length ? (
          <p className="p-4 text-center text-sm text-ink-muted">Здесь появятся шаги агента после первого сообщения.</p>
        ) : (
          <div className="space-y-4">
            {groups.map((g, gi) => (
              <section key={gi}>
                <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                  {g.version != null ? `Ход → версия плана ${g.version}` : 'Текущий ход'}
                </div>
                <ol className="relative space-y-0.5 before:absolute before:bottom-3 before:left-[9px] before:top-3 before:w-px before:bg-black/10">
                  {g.steps.map((s, i) => (
                    <StepRow key={i} s={s} n={i + 1} />
                  ))}
                </ol>
              </section>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
