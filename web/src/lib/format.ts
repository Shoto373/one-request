import type { CheckStatus, DestinationOption, OfferKind, PriceUnit, Requirement, RequirementKind } from '../api/types'

export function rub(n: number): string {
  return new Intl.NumberFormat('ru-RU').format(Math.round(n)) + ' ₽'
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
export const MONTH_NAME = ['январе', 'феврале', 'марте', 'апреле', 'мае', 'июне', 'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре']

/**
 * Время показываем В ЧАСОВОМ ПОЯСЕ САМОГО СОБЫТИЯ (как в билете), а не браузера:
 * '2026-11-10T07:30:00+03:00' → { date: '10 ноя', time: '07:30', tz: '+03:00' }.
 */
export function localParts(iso: string): { date: string; time: string; tz: string } {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/)
  if (!m) return { date: iso, time: '', tz: '' }
  return { date: `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}`, time: `${m[4]}:${m[5]}`, tz: m[6] === 'Z' ? 'UTC' : m[6] ?? '' }
}

export function when(iso: string): string {
  const p = localParts(iso)
  return `${p.date}, ${p.time}`
}

/** 245 → «4 ч 05 мин» */
export function duration(min: number): string {
  const h = Math.floor(min / 60)
  const m = min % 60
  return h ? `${h} ч${m ? ` ${String(m).padStart(2, '0')} мин` : ''}` : `${m} мин`
}

export const KIND_LABEL: Record<OfferKind, string> = { flight: 'Перелёт', train: 'Поезд', transfer: 'Трансфер', hotel: 'Отель' }

export const PRICE_UNIT: Record<PriceUnit, string> = {
  per_room_night: 'за номер в ночь',
  per_person: 'за человека',
  per_vehicle: 'за машину',
  per_trip: 'за поездку',
}

export const REQ_KIND: Record<RequirementKind, { label: string; cls: string; dot: string; hint: string }> = {
  hard: {
    label: 'обязательно', cls: 'border-brand bg-brand text-white', dot: 'bg-brand',
    hint: 'Вы так сказали — не нарушаем без вашего согласия',
  },
  soft: {
    label: 'желательно', cls: 'border-brand/25 bg-brand-light text-brand', dot: 'bg-brand/40',
    hint: 'Можем предложить компромисс и скажем об этом',
  },
  assumption: {
    label: 'догадка агента', cls: 'border-dashed border-warn/60 bg-warn-bg text-[#8a4b00]', dot: 'bg-warn',
    hint: 'Агент предположил — подтвердите или исправьте',
  },
  unknown: {
    label: 'неизвестно', cls: 'border-dashed border-ink-line bg-white text-ink-muted', dot: 'bg-ink-muted',
    hint: 'Спросим, только если влияет на расчёт',
  },
}

export const CHECK_STYLE: Record<CheckStatus, { cls: string; icon: 'check' | 'alert' | 'x' }> = {
  ok: { cls: 'text-ok bg-ok-bg', icon: 'check' },
  warn: { cls: 'text-warn bg-warn-bg', icon: 'alert' },
  block: { cls: 'text-bad bg-bad-bg', icon: 'x' },
}

/** Подпись требования: label от агента или человеческий вывод value */
export function reqLabel(r: Requirement): string {
  if (r.label) return r.label
  const v = r.value
  if (v == null) return r.key
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    if ('from' in o && 'to' in o) return `${o.from} — ${o.to}`
    if ('adults' in o) return `${o.adults} взр.${Array.isArray(o.children_ages) && o.children_ages.length ? ` + ${o.children_ages.length} реб.` : ''}`
    return Object.values(o).join(', ')
  }
  if (typeof v === 'boolean') return v ? r.key : `без ${r.key}`
  if (/rub/.test(r.key) && typeof v === 'number') return `до ${rub(v)}`
  return String(v)
}

export const VISA_LABEL: Record<NonNullable<DestinationOption['visa']>['regime'], { text: string; cls: string }> = {
  domestic: { text: 'Без загранпаспорта', cls: 'bg-lime-soft text-[#2f6b0f]' },
  visa_free: { text: 'Без визы', cls: 'bg-lime-soft text-[#2f6b0f]' },
  visa_on_arrival: { text: 'Виза по прилёте', cls: 'bg-warn-bg text-[#8a4b00]' },
  evisa: { text: 'Электронная виза', cls: 'bg-warn-bg text-[#8a4b00]' },
  visa_required: { text: 'Нужна виза', cls: 'bg-bad-bg text-bad' },
}

export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}
