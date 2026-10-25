import { useEffect, useRef, useState } from 'react'
import type { Requirement, RequirementKind } from '../api/types'
import { REQ_KIND, rub } from '../lib/format'
import Icon from './Icon'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface Props {
  requirements: Requirement[]
  busy: boolean
  onEdit: (r: Requirement, text: string) => void
  onRemove: (r: Requirement) => void
  onConfirm: (r: Requirement) => void
}

const ORDER: RequirementKind[] = ['hard', 'soft', 'assumption', 'unknown']
const FIELD: Record<string, string> = {
  origin: 'Город отправления', destination: 'Город поездки', dates: 'Даты поездки', travel_start_date: 'Дата выезда',
  travel_month: 'Месяц поездки', duration_nights: 'Длительность', date_flex_days: 'Гибкость дат', adults: 'Взрослые',
  children_count: 'Дети', children_ages: 'Возраст детей', pets: 'Животное', pet_weight_kg: 'Вес животного',
  budget_total_rub: 'Бюджет на поездку', budget_per_person_rub: 'Бюджет на человека', budget_scope: 'Основание бюджета',
  budget_preference: 'Бюджетное пожелание', transport: 'Транспорт', max_stops: 'Пересадки',
  travel_time_preference: 'Время в дороге', max_duration_min: 'Максимум в пути', trip_style: 'Стиль поездки',
  lodging_stars: 'Категория отеля', meal_preference: 'Питание', room_preference: 'Номер',
  assumption_child_flight_time: 'Время перелёта с ребёнком', assumption_child_menu: 'Питание ребёнка',
  travel_year: 'Год поездки',
}

function shortDate(value: unknown): string {
  if (typeof value !== 'string') return String(value ?? '')
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value
}

function valueText(r: Requirement, configuredDestinations: Record<string, string>): string {
  const value = r.value
  if (value == null || value === '') return 'Не указано'
  if (r.key === 'origin' && value === 'MOW') return 'Москва'
  if (r.key === 'destination' && typeof value === 'string' && configuredDestinations[value]) return configuredDestinations[value]
  if (r.key === 'dates' && typeof value === 'object') {
    const v = value as Record<string, unknown>
    const start = v.start ?? v.from
    const end = v.end ?? v.to
    if (start || end) return `${shortDate(start)} — ${shortDate(end)}`
  }
  if (r.key === 'travel_start_date') return shortDate(value)
  if (r.key === 'adults' && typeof value === 'number') return `${value} ${value === 1 ? 'взрослый' : 'взрослых'}`
  if (r.key === 'children_count' && typeof value === 'number') return `${value} ${value === 1 ? 'ребёнок' : 'детей'}`
  if (r.key === 'children_ages' && Array.isArray(value)) return value.map((age) => `${age} лет`).join(', ')
  if (r.key === 'pets' && typeof value === 'object') {
    const kind = (value as Record<string, unknown>).kind
    return kind === 'dog' ? 'Собака' : kind === 'cat' ? 'Кошка' : String(kind ?? 'Питомец')
  }
  if (r.key === 'pet_weight_kg') return `${value} кг`
  if (r.key.includes('rub') && typeof value === 'number') return rub(value)
  if (r.key === 'budget_scope') return value === 'trip' ? 'На всю поездку' : value === 'person' ? 'На человека' : String(value)
  if (r.key === 'transport') return value === 'train' ? 'Поезд' : value === 'flight' ? 'Самолёт' : String(value)
  if (r.key === 'duration_nights' && typeof value === 'number') return `${value} ${value === 1 ? 'ночь' : value >= 2 && value <= 4 ? 'ночи' : 'ночей'}`
  if (r.key === 'max_duration_min' && typeof value === 'number') return `${Math.floor(value / 60)} ч ${value % 60 ? `${value % 60} мин` : ''}`.trim()
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет'
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') return Object.values(value as Record<string, unknown>).map(shortDate).join(' — ')
  return String(value)
}

function sourceText(r: Requirement): string {
  if (r.kind === 'assumption') return r.label || 'Предположение агента'
  if (r.source === 'user_text') return 'Из вашего сообщения'
  if (r.source === 'user_edit') return 'Исправлено вами'
  if (r.source === 'memory') return 'Из прошлой поездки'
  if (r.source === 'agent') return 'Определено агентом'
  return 'Источник не указан'
}

/** Табличный разбор требований: поле, значение, статус и источник показаны отдельно. */
export default function Requirements({ requirements, busy, onEdit, onRemove, onConfirm }: Props) {
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 60_000 })
  const configuredDestinations = Object.fromEntries((status?.product_coverage?.destinations ?? []).map((city) => [city.id, city.name]))
  const [editing, setEditing] = useState<string | null>(null)
  const sorted = [...requirements].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
  const assumptions = requirements.filter((r) => r.kind === 'assumption').length

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between gap-3 px-4 pb-3 pt-4 md:px-5">
        <div>
          <h2 className="text-[17px] font-bold">Что агент понял</h2>
          <p className="mt-0.5 text-xs text-ink-muted">Проверьте значения и исправьте, если агент понял вас не так.</p>
        </div>
        {assumptions > 0 && <span className="shrink-0 text-xs font-medium text-[#8a4b00]">{assumptions} догадки — проверьте</span>}
      </div>

      {!requirements.length ? (
        <p className="px-5 pb-5 text-sm text-ink-muted">Пока нет условий поездки.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-left text-sm">
            <thead className="bg-canvas/80 text-[11px] uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Условие</th>
                <th className="px-4 py-2.5 font-semibold">Что записано</th>
                <th className="px-4 py-2.5 font-semibold">Тип</th>
                <th className="px-4 py-2.5 font-semibold">Откуда</th>
                <th className="px-4 py-2.5 text-right font-semibold">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[.06]">
              {sorted.map((r) => (
                <tr key={r.key} className={r.kind === 'assumption' ? 'bg-warn-bg/25' : 'bg-white'}>
                  <td className="px-5 py-3 font-medium text-ink">{FIELD[r.key] ?? r.key}</td>
                  <td className="px-4 py-3 text-ink-grey">
                    {editing === r.key ? (
                      <EditValue
                        r={r}
                        configuredDestinations={configuredDestinations}
                        onCancel={() => setEditing(null)}
                        onSave={(text) => { setEditing(null); onEdit(r, text) }}
                      />
                    ) : <span>{valueText(r, configuredDestinations)}</span>}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${kindStyle(r.kind)}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${REQ_KIND[r.kind].dot}`} />
                      {REQ_KIND[r.kind].label}
                    </span>
                  </td>
                  <td className="max-w-[240px] px-4 py-3 text-xs text-ink-muted">{sourceText(r)}</td>
                  <td className="px-4 py-2.5">
                    {editing !== r.key && (
                      <div className="flex justify-end gap-1">
                        {r.kind === 'assumption' && (
                          <button onClick={() => onConfirm(r)} disabled={busy} className="btn-ghost h-8 gap-1 px-2 text-xs text-ok" title="Подтвердить догадку">
                            <Icon name="check" className="h-3.5 w-3.5" /> Верно
                          </button>
                        )}
                        <button onClick={() => setEditing(r.key)} disabled={busy} className="btn-ghost h-8 gap-1 px-2 text-xs" title="Исправить значение">
                          <Icon name="pencil" className="h-3.5 w-3.5" /> Изменить
                        </button>
                        {r.kind !== 'hard' && (
                          <button onClick={() => onRemove(r)} disabled={busy} className="btn-ghost h-8 px-2 text-xs text-ink-muted" title="Убрать условие">
                            Убрать
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-black/[.06] px-5 py-2.5 text-[11px] text-ink-muted">
        {(['hard', 'soft', 'assumption'] as const).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5" title={REQ_KIND[k].hint}>
            <span className={`h-2 w-2 rounded-full ${REQ_KIND[k].dot}`} />{REQ_KIND[k].label}
          </span>
        ))}
      </div>
    </section>
  )
}

function kindStyle(kind: RequirementKind): string {
  if (kind === 'hard') return 'bg-brand-light text-brand'
  if (kind === 'soft') return 'bg-[#f1f4ff] text-[#5c68ad]'
  if (kind === 'assumption') return 'bg-warn-bg text-[#8a4b00]'
  return 'bg-canvas text-ink-muted'
}

function EditValue({ r, configuredDestinations, onSave, onCancel }: {
  r: Requirement; configuredDestinations: Record<string, string>; onSave: (text: string) => void; onCancel: () => void
}) {
  const [value, setValue] = useState(valueText(r, configuredDestinations))
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => input.current?.select(), [])
  return (
    <form className="flex min-w-[280px] gap-1" onSubmit={(e) => { e.preventDefault(); if (value.trim()) onSave(value.trim()) }}>
      <input ref={input} value={value} onChange={(e) => setValue(e.target.value)} className="w-full rounded-lg border border-brand/30 px-2 py-1 text-sm outline-none focus:border-brand" />
      <button type="submit" className="rounded-lg bg-brand px-2 text-white" title="Сохранить"><Icon name="check" className="h-4 w-4" /></button>
      <button type="button" onClick={onCancel} className="rounded-lg px-2 text-ink-muted hover:bg-black/5" title="Отмена"><Icon name="x" className="h-4 w-4" /></button>
    </form>
  )
}
