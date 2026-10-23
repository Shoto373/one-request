/**
 * Что изменила пересборка: сравнение двух версий плана по offer_id.
 * Сохранено — та же позиция; заменено — позиция того же вида с другим предложением.
 * Это вычисление по фактическим состояниям, поэтому не зависит от того, что агент написал в history.
 */
import type { Bundle, HistoryEntry, Offer, TripState } from '../api/types'

export interface ItemChange {
  kind: Offer['kind']
  before?: Offer
  after?: Offer
}

export interface PlanDiff {
  fromVersion: number
  toVersion: number
  label: string
  kept: Offer[]
  replaced: ItemChange[]
  added: Offer[]
  removed: Offer[]
  priceBefore: number
  priceAfter: number
  /** запись агента в history, если есть: что изменилось и почему */
  entry?: HistoryEntry
}

function pick(s: TripState): Bundle | undefined {
  return s.bundles.find((b) => b.id === s.selected_bundle_id) ?? s.bundles[0]
}

/** сопоставляем позиции по виду и порядку (туда/обратно), затем смотрим, то же ли предложение */
export function diffPlans(prev: TripState, next: TripState): PlanDiff | null {
  const b = pick(next)
  // тот же комплект в прошлой версии: смена выбора между комплектами — не пересборка
  const a = b && (prev.bundles.find((x) => x.id === b.id) ?? pick(prev))
  if (!a || !b || prev.version === next.version) return null

  const kept: Offer[] = []
  const replaced: ItemChange[] = []
  const added: Offer[] = []
  const removed: Offer[] = []

  const byKind = (xs: Offer[]) => {
    const m = new Map<string, Offer[]>()
    for (const o of xs) m.set(o.kind === 'train' ? 'flight' : o.kind, [...(m.get(o.kind === 'train' ? 'flight' : o.kind) ?? []), o])
    return m
  }
  const before = byKind(a.items)
  const after = byKind(b.items)
  for (const kind of new Set([...before.keys(), ...after.keys()])) {
    const xs = before.get(kind) ?? []
    const ys = after.get(kind) ?? []
    for (let i = 0; i < Math.max(xs.length, ys.length); i++) {
      const x = xs[i]
      const y = ys[i]
      if (x && y) {
        if (x.offer_id === y.offer_id) kept.push(y)
        else replaced.push({ kind: y.kind, before: x, after: y })
      } else if (y) added.push(y)
      else if (x) removed.push(x)
    }
  }
  if (!replaced.length && !added.length && !removed.length) return null

  const entry = [...(next.history ?? [])].reverse().find((h) => h.version === next.version) ?? next.history?.[next.history.length - 1]
  return {
    fromVersion: prev.version,
    toVersion: next.version,
    label: b.label,
    kept,
    replaced,
    added,
    removed,
    priceBefore: a.total_rub,
    priceAfter: b.total_rub,
    entry,
  }
}
