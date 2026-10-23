/**
 * Сравнение комплектов по критериям ЭТОГО пользователя — только из данных предложений (никаких догадок).
 * Порядок строк зависит от требований: бюджет → цена выше, ребёнок → время в пути и прибытие, и т.д.
 */
import type { Bundle, Offer, Requirement } from '../api/types'
import { duration, rub } from './format'

export type Better = 'min' | 'max'

export interface CompareRow {
  key: string
  label: string
  /** почему строка важна этому пользователю (из его требований) */
  why?: string
  better: Better | null
  cells: { value: number | null; text: string; note?: string }[]
  /** индексы комплектов с лучшим значением (если различаются) */
  best: number[]
}

const minutes = (o: Offer) =>
  o.times ? Math.round((Date.parse(o.times.end) - Date.parse(o.times.start)) / 60000) : 0

const isTransport = (o: Offer) => o.kind === 'flight' || o.kind === 'train'

/** часы в пути туда и обратно: транспорт + трансферы */
function travelMinutes(b: Bundle): number {
  return b.items.filter((o) => isTransport(o) || o.kind === 'transfer').reduce((s, o) => s + minutes(o), 0)
}

function stops(b: Bundle): number {
  return b.items
    .filter(isTransport)
    .reduce((s, o) => s + (typeof o.variant?.stops === 'number' ? (o.variant.stops as number) : 0), 0)
}

/** сколько можно вернуть при отмене прямо сейчас: доля позиций с бесплатной отменой (по деньгам) */
function refundableShare(b: Bundle): number {
  const total = b.items.reduce((s, o) => s + o.total_rub, 0) || 1
  const free = b.items.filter((o) => o.cancel_policy?.free_until).reduce((s, o) => s + o.total_rub, 0)
  return free / total
}

function meal(b: Bundle): string {
  const hotel = b.items.find((o) => o.kind === 'hotel')
  const inc = hotel?.included ?? []
  if (inc.some((x) => /all|всё/i.test(x))) return 'всё включено'
  const parts = [
    inc.some((x) => /breakfast|завтрак/i.test(x)) && 'завтрак',
    inc.some((x) => /dinner|ужин/i.test(x)) && 'ужин',
  ].filter(Boolean)
  return parts.length ? parts.join(' + ') : 'без питания'
}
const mealScore = (t: string) => (t === 'всё включено' ? 3 : t.includes('+') ? 2 : t === 'без питания' ? 0 : 1)

const has = (reqs: Requirement[], re: RegExp) => reqs.some((r) => re.test(r.key) && r.kind !== 'unknown')

function bestOf(values: (number | null)[], better: Better | null): number[] {
  if (!better) return []
  const nums = values.filter((v): v is number => v != null)
  if (nums.length < 2 || nums.every((v) => v === nums[0])) return []
  const target = better === 'min' ? Math.min(...nums) : Math.max(...nums)
  return values.flatMap((v, i) => (v === target ? [i] : []))
}

export function compareBundles(bundles: Bundle[], reqs: Requirement[]): CompareRow[] {
  const withKids = has(reqs, /child|kids|travelers/) && JSON.stringify(reqs).match(/child/)
  const withPets = has(reqs, /pet/)
  const budget = reqs.find((r) => /budget/.test(r.key) && typeof r.value === 'number')

  const rows: (Omit<CompareRow, 'best'> & { weight: number })[] = []

  rows.push({
    key: 'total', label: 'Итого за всех', better: 'min',
    why: budget ? `ваш бюджет — до ${rub(budget.value as number)}` : undefined,
    weight: budget ? 100 : 90,
    cells: bundles.map((b) => ({
      value: b.total_rub,
      text: rub(b.total_rub),
      note: budget ? (b.total_rub <= (budget.value as number) ? 'в бюджете' : `+${rub(b.total_rub - (budget.value as number))}`) : undefined,
    })),
  })

  rows.push({
    key: 'travel', label: 'В дороге туда и обратно', better: 'min',
    why: withKids ? 'с ребёнком важна короткая дорога' : has(reqs, /flight_hours|duration|max_duration/) ? 'вы просили недолгую дорогу' : undefined,
    weight: withKids || has(reqs, /flight_hours|duration/) ? 95 : 70,
    cells: bundles.map((b) => {
      const m = travelMinutes(b)
      return { value: m || null, text: m ? duration(m) : '—' }
    }),
  })

  const anyStops = bundles.some((b) => stops(b) > 0)
  if (anyStops || has(reqs, /stops/)) {
    rows.push({
      key: 'stops', label: 'Пересадки', better: 'min', weight: has(reqs, /stops/) ? 92 : 60,
      why: has(reqs, /stops/) ? 'вы просили без пересадок' : undefined,
      cells: bundles.map((b) => ({ value: stops(b), text: stops(b) ? String(stops(b)) : 'прямые' })),
    })
  }

  rows.push({
    key: 'cancel', label: 'Можно отменить бесплатно', better: 'max', weight: has(reqs, /cancel|flex/) ? 93 : 65,
    why: has(reqs, /cancel|flex/) ? 'вам важна гибкость' : undefined,
    cells: bundles.map((b) => {
      const share = refundableShare(b)
      return { value: Math.round(share * 100), text: share >= 0.999 ? 'всё' : share <= 0.001 ? 'ничего' : `${Math.round(share * 100)}% суммы` }
    }),
  })

  rows.push({
    key: 'meal', label: 'Питание в отеле', better: 'max', weight: has(reqs, /meal/) ? 91 : 55,
    why: has(reqs, /meal/) ? 'вы указали питание' : undefined,
    cells: bundles.map((b) => {
      const m = meal(b)
      return { value: mealScore(m), text: m }
    }),
  })

  rows.push({
    key: 'checks', label: 'Стыковки', better: 'min', weight: 80,
    why: 'прилёт, трансфер, заселение и обратная дорога',
    cells: bundles.map((b) => {
      const block = b.checks.filter((c) => c.status === 'block').length
      const warn = b.checks.filter((c) => c.status === 'warn').length
      return {
        value: block * 10 + warn,
        text: block ? `${block} блок.` : warn ? `${warn} на заметку` : 'всё сходится',
      }
    }),
  })

  if (withPets) {
    rows.push({
      key: 'pets', label: 'С животным', better: 'min', weight: 94, why: 'вы едете с животным',
      cells: bundles.map((b) => {
        const bad = b.checks.filter((c) => c.kind === 'pets' && c.status !== 'ok').length
        return { value: bad, text: bad ? 'есть ограничения' : 'можно везде' }
      }),
    })
  }

  return rows
    .sort((a, b) => b.weight - a.weight)
    .map(({ weight: _w, ...r }) => ({ ...r, best: bestOf(r.cells.map((c) => c.value), r.better) }))
}
