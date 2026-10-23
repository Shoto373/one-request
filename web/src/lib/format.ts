import type { CheckStatus, OfferKind, PriceUnit, RequirementKind } from '../api/types'

export function rub(n: number): string {
  return new Intl.NumberFormat('ru-RU').format(Math.round(n)) + ' ₽'
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

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

export const KIND_ICON: Record<OfferKind, string> = { flight: '✈️', train: '🚆', transfer: '🚕', hotel: '🏨' }

export const PRICE_UNIT: Record<PriceUnit, string> = {
  per_room_night: 'за номер в ночь',
  per_person: 'за человека',
  per_vehicle: 'за машину',
  per_trip: 'за поездку',
}

export const REQ_KIND: Record<RequirementKind, { label: string; cls: string; hint: string }> = {
  hard: { label: 'обязательно', cls: 'border-slate-800 bg-slate-800 text-white', hint: 'Вы так сказали — не нарушаем без вашего согласия' },
  soft: { label: 'желательно', cls: 'border-brand bg-brand-light text-brand-dark', hint: 'Можем предложить компромисс' },
  assumption: { label: 'предположение', cls: 'border-dashed border-amber-400 bg-amber-50 text-amber-800', hint: 'Агент догадался — исправьте, если не так' },
  unknown: { label: 'неизвестно', cls: 'border-dashed border-slate-300 bg-white text-slate-500', hint: 'Спросим, если повлияет на расчёт' },
}

export const CHECK_STYLE: Record<CheckStatus, { icon: string; cls: string }> = {
  ok: { icon: '✓', cls: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  warn: { icon: '!', cls: 'text-amber-800 bg-amber-50 border-amber-200' },
  block: { icon: '✕', cls: 'text-rose-800 bg-rose-50 border-rose-200' },
}
