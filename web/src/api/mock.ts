/**
 * Мок бэкенда для `npm run dev:mock`: агент, корзина и заказы в памяти браузера.
 * Повторяет форму реального API (SSE app/main.py, версии и 409, идемпотентность оплаты)
 * и проигрывает сценарии спринта 2: размытый запрос → уточнения → направления → комплекты;
 * невыполнимый запрос → «вариантов нет, вот минимальные изменения». Данные вымышленные.
 */
import type {
  Bundle, Cart, CartIn, ChatMessage, CheckoutIn, CommandIn, DestinationOption, MessageIn, Offer, Order, ProposedChange,
  Requirement, SessionCreated, SessionView, StreamEvent, TraceStep, TripState,
} from './types'
import type { Api } from './client'
import { ApiError } from './errors'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const uid = () => crypto.randomUUID()

interface MockSession {
  state: TripState
  messages: ChatMessage[]
  trace: TraceStep[]
}
const sessions = new Map<string, MockSession>()
const carts = new Map<string, Cart>()
const orders = new Map<string, Order>()
const ordersByKey = new Map<string, string>()

// ---------- каталог-заглушка ----------

let seq = 0
function offer(o: Partial<Offer> & Pick<Offer, 'kind' | 'title' | 'total_rub'>): Offer {
  return {
    offer_id: `off_${o.kind}_${++seq}`,
    item_id: o.kind,
    quantity: 1,
    unit_price_rub: o.total_rub,
    price_unit: 'per_trip',
    units: 1,
    synthetic: true,
    ...o,
  }
}
const sum = (xs: Offer[]) => xs.reduce((s, x) => s + x.total_rub, 0)

function bundlesSochi(): Bundle[] {
  const hotel = offer({
    kind: 'hotel', title: 'Ривьера Парк · Стандарт, завтрак', total_rub: 54600, unit_price_rub: 7800,
    price_unit: 'per_room_night', units: 7, included: ['завтрак', 'wi-fi'],
    cancel_policy: { free_until: '2026-11-07T23:59:00+03:00', penalty_rub: 7800 },
    times: { start: '2026-11-10T14:00:00+03:00', end: '2026-11-17T12:00:00+03:00' },
  })
  const fast: Offer[] = [
    offer({ kind: 'flight', title: 'SU 1120 · Шереметьево → Сочи', total_rub: 19800, unit_price_rub: 9900, price_unit: 'per_person', units: 2, baggage: '1 × 23 кг', times: { start: '2026-11-10T07:30:00+03:00', end: '2026-11-10T10:05:00+03:00' }, cancel_policy: { free_until: null, penalty_rub: 19800 } }),
    offer({ kind: 'transfer', title: 'Такси · аэропорт → отель', total_rub: 2400, price_unit: 'per_vehicle', times: { start: '2026-11-10T10:25:00+03:00', end: '2026-11-10T11:05:00+03:00' } }),
    hotel,
    offer({ kind: 'transfer', title: 'Такси · отель → аэропорт', total_rub: 2400, price_unit: 'per_vehicle', times: { start: '2026-11-17T12:30:00+03:00', end: '2026-11-17T13:10:00+03:00' } }),
    offer({ kind: 'flight', title: 'SU 1125 · Сочи → Шереметьево', total_rub: 21400, unit_price_rub: 10700, price_unit: 'per_person', units: 2, baggage: '1 × 23 кг', times: { start: '2026-11-17T15:20:00+03:00', end: '2026-11-17T17:55:00+03:00' } }),
  ]
  const cheap: Offer[] = [
    offer({ kind: 'train', title: 'Поезд 104В · Москва → Сочи, купе', total_rub: 14600, unit_price_rub: 7300, price_unit: 'per_person', units: 2, times: { start: '2026-11-09T09:10:00+03:00', end: '2026-11-10T09:58:00+03:00' } }),
    offer({ kind: 'transfer', title: 'Такси · вокзал → отель', total_rub: 900, price_unit: 'per_vehicle', times: { start: '2026-11-10T10:10:00+03:00', end: '2026-11-10T10:25:00+03:00' } }),
    hotel,
    offer({ kind: 'transfer', title: 'Такси · отель → вокзал', total_rub: 900, price_unit: 'per_vehicle', times: { start: '2026-11-17T12:30:00+03:00', end: '2026-11-17T12:45:00+03:00' } }),
    offer({ kind: 'train', title: 'Поезд 103С · Сочи → Москва, купе', total_rub: 15200, unit_price_rub: 7600, price_unit: 'per_person', units: 2, times: { start: '2026-11-17T14:05:00+03:00', end: '2026-11-18T15:20:00+03:00' } }),
  ]
  const early = (at: string) => ({
    kind: 'early_arrival', status: 'warn' as const, text: `В отеле в ${at}, заселение с 14:00`,
    options: ['Оставить багаж на стойке — бесплатно', 'Ранний заезд с 11:00 — 2 500 ₽, не гарантирован'],
  })
  return [
    {
      id: 'b-cheap', label: 'дешевле', items: cheap, total_rub: sum(cheap),
      checks: [early('10:25'), { kind: 'return_trip', status: 'ok', text: 'Выезд в 12:00, поезд в 14:05 — запас 1 ч 20 мин' }, { kind: 'budget', status: 'ok', text: 'Укладывается в бюджет' }],
      tradeoffs: [{ text: 'Сутки в поезде в каждую сторону — минус почти два дня отдыха', refs: ['offer:train'] }],
      risks: [],
    },
    {
      id: 'b-fast', label: 'быстрее', items: fast, total_rub: sum(fast),
      checks: [early('11:05'), { kind: 'return_trip', status: 'ok', text: 'Выезд в 12:00, вылет в 15:20 — в аэропорту за 2 ч 10 мин' }, { kind: 'budget', status: 'ok', text: 'Укладывается в бюджет' }],
      tradeoffs: [{ text: 'Дороже на 14 400 ₽, зато 2,5 часа в пути вместо суток', refs: ['offer:flight', 'offer:train'] }],
      risks: [{ text: 'Невозвратный тариф перелёта', refs: ['offer:SU1120.cancel_policy'] }],
    },
  ]
}

const DESTINATIONS: DestinationOption[] = [
  {
    id: 'AYT', name: 'Анталья', country: 'Турция', feasible: true, est_total_rub: 142000,
    climate: { month: 11, air_temp_day: 22, sea_temp: 22, rainy_days: 6 }, travel: { kind: 'flight', duration_min: 245, stops: 0, title: 'Pegasus PC 395' },
    visa: { regime: 'visa_free', note: 'до 60 дней, загранпаспорт' }, tags: ['море', 'пляж', 'всё включено'],
    why: [{ text: 'Море +22 °C — ещё купаются', refs: ['climate:AYT:11.sea_temp'] }, { text: 'Прямой рейс 4 ч 05 мин', refs: ['offer:flight'] }, { text: 'Много отелей с детским клубом', refs: ['catalog:hotels:AYT'] }],
    risks: [{ text: 'В ноябре 6 дождливых дней', refs: ['climate:AYT:11.rainy_days'] }],
  },
  {
    id: 'SSH', name: 'Шарм-эль-Шейх', country: 'Египет', feasible: true, est_total_rub: 168000,
    climate: { month: 11, air_temp_day: 29, sea_temp: 26, rainy_days: 0 }, travel: { kind: 'flight', duration_min: 320, stops: 0, title: 'Azur Air ZF 676' },
    visa: { regime: 'visa_on_arrival', note: 'виза по прилёте, 25 $' }, tags: ['море', 'риф', 'жара'],
    why: [{ text: 'Самое тёплое море из вариантов, +26 °C', refs: ['climate:SSH:11.sea_temp'] }, { text: 'Дождей почти нет', refs: ['climate:SSH:11.rainy_days'] }],
    risks: [{ text: 'Перелёт 5 ч 20 мин — длиннее, чем вы хотели', refs: ['offer:flight'] }, { text: 'Виза по прилёте — очередь в аэропорту', refs: ['visa:SSH'] }],
  },
  {
    id: 'AER', name: 'Сочи', country: 'Россия', feasible: true, est_total_rub: 96000,
    climate: { month: 11, air_temp_day: 15, sea_temp: 16, rainy_days: 12 }, travel: { kind: 'flight', duration_min: 155, stops: 0, title: 'Аэрофлот SU 1120' },
    visa: { regime: 'domestic' }, tags: ['море', 'горы', 'без загранпаспорта'],
    why: [{ text: 'Дешевле всех — около 96 000 ₽ на троих', refs: ['estimate:AER'] }, { text: 'Лететь 2 ч 35 мин, можно поездом', refs: ['offer:flight'] }],
    risks: [{ text: 'Море +16 °C — купаться холодно', refs: ['climate:AER:11.sea_temp'] }, { text: '12 дождливых дней', refs: ['climate:AER:11.rainy_days'] }],
  },
]

// ---------- «агент» ----------

type Turn = { steps: TraceStep[]; patch: Partial<TripState>; reply: string }

const step = (step_type: string, name: string, input?: unknown, output?: unknown): TraceStep => ({ step_type, name, input, output, status: 'ok' })
const req = (key: string, value: unknown, kind: Requirement['kind'], label: string, quote?: string): Requirement =>
  ({ key, value, kind, source: kind === 'assumption' ? 'agent' : 'user_text', quote, label })

function upsert(list: Requirement[], items: Requirement[]): Requirement[] {
  const keys = new Set(items.map((r) => r.key))
  return [...list.filter((r) => !keys.has(r.key)), ...items]
}

function think(s: MockSession, text: string): Turn {
  const t = text.toLowerCase()
  const st = s.state

  // невыполнимый запрос
  if (/дуба/.test(t) && /30\s*(000|тыс)/.test(t)) {
    const proposed_changes: ProposedChange[] = [
      { id: 'alt-destination-1', kind: 'destination', label: 'Другое направление — Сочи — 53 786 ₽ (+23 786 ₽ к бюджету)', total_rub: 53786, over_budget_rub: 23786, needs_consent: false, changes: [{ key: 'destination', from: 'DXB', to: 'AER', to_name: 'Сочи' }] },
      { id: 'alt-budget-2', kind: 'budget', label: 'Поднять бюджет до 349 500 ₽ — Дубай, как вы хотели', total_rub: 349500, over_budget_rub: 319500, needs_consent: true, changes: [{ key: 'budget_total_rub', from: 30000, to: 349500 }] },
    ]
    return {
      steps: [step('llm', 'parse_request', { text }), step('tool', 'build_bundles', { dest: 'DXB', budget: 30000 }, { bundles: 0 }), step('tool', 'explain_no_bundles', {}, { cheapest_total_rub: 349500 }), step('tool', 'suggest_destinations', {}, { alternatives: 2 })],
      patch: {
        stage: 'select',
        requirements: [req('origin', 'Москва', 'hard', 'Из Москвы'), req('destination', 'DXB', 'hard', 'Дубай', 'Дубай'), req('nights', 7, 'hard', '7 ночей', 'на неделю'), req('travelers', { adults: 3 }, 'hard', '3 взрослых', 'на троих'), req('budget_total_rub', 30000, 'hard', 'Бюджет до 30 000 ₽', 'за 30 000')],
        bundles: [], destinations: [], open_questions: [], proposed_changes,
      },
      reply: 'Честно: под все условия вариантов нет — неделя в Дубае на троих стоит от 349 500 ₽. Бюджет сам не повышаю. Предлагаю два минимальных изменения — выберите, если подходит.',
    }
  }

  // размытый запрос → уточнения
  if (st.stage === 'clarify' && !st.requirements.length && /(т[её]пл|мор)/.test(t) && !/из\s/.test(t)) {
    return {
      steps: [step('llm', 'parse_request', { text }, { requirements: 6 }), step('tool', 'next_questions', {}, { questions: 2 })],
      patch: {
        requirements: [
          req('month', 11, 'hard', 'Ноябрь', 'в ноябре'), req('sea', true, 'hard', 'Тёплое море', 'к тёплому морю'),
          req('budget_level', 'low', 'soft', 'Недорого', 'недорого'), req('travelers', { adults: 2, children: 1 }, 'hard', '2 взрослых + ребёнок', 'с ребёнком'),
          req('max_flight_hours', 5, 'soft', 'Перелёт до ~5 ч', 'без долгих перелётов'), req('daytime_flights', true, 'assumption', 'Дневные рейсы — удобнее с ребёнком'),
        ],
        open_questions: [
          { key: 'origin', question: 'Откуда летите?', why: 'от города вылета зависят рейсы и цена', options: ['Москва', 'Санкт-Петербург', 'Екатеринбург'] },
          { key: 'child_age', question: 'Сколько лет ребёнку?', why: 'до 2 лет перелёт почти бесплатный, в отеле — детский тариф', options: ['до 2 лет', '2–6 лет', '7–12 лет'] },
        ],
      },
      reply: 'Понял: тёплое море в ноябре, недорого, с ребёнком, перелёт покороче. Чтобы посчитать честно, уточню два момента.',
    }
  }

  // ответы на вопросы → направления
  if (st.open_questions.length) {
    const origin = /петербург|спб/.test(t) ? 'Санкт-Петербург' : /екатеринбург/.test(t) ? 'Екатеринбург' : /москв/.test(t) ? 'Москва' : null
    const age = t.match(/(до 2|2–6|7–12)/)?.[1]
    const reqs = [
      ...(origin ? [req('origin', origin, 'hard', `Из: ${origin}`, origin)] : []),
      ...(age ? [req('travelers', { adults: 2, children_ages: [age === 'до 2' ? 1 : age === '2–6' ? 5 : 9] }, 'hard', `2 взрослых + ребёнок ${age} лет`, age)] : []),
    ]
    const requirements = upsert(st.requirements, reqs)
    const left = st.open_questions.filter((q) => !(q.key === 'origin' && origin) && !(q.key === 'child_age' && age))
    if (left.length) {
      return { steps: [step('llm', 'parse_answer', { text })], patch: { requirements, open_questions: left }, reply: 'Записал. Остался один вопрос.' }
    }
    return {
      steps: [step('llm', 'parse_answer', { text }), step('tool', 'get_climate', { month: 11 }), step('tool', 'suggest_destinations', { month: 11, sea: true }, { candidates: 8, feasible: 3 }), step('tool', 'build_bundles', { probe: ['AYT', 'SSH', 'AER'] }, { ok: 3 })],
      patch: { stage: 'inspire', requirements, open_questions: [], destinations: DESTINATIONS },
      reply: 'Вот три направления, где поездка точно собирается. Анталья — лучший баланс тепла, цены и времени в пути; Шарм теплее, но лететь дольше; Сочи дешевле, но купаться уже холодно.',
    }
  }

  // выбор направления или конкретный запрос → комплекты
  if (/сочи|анталь|шарм|выбира/.test(t) || /из\s/.test(t)) {
    const reqs = /из москв/.test(t)
      ? [req('origin', 'Москва', 'hard', 'Из Москвы', 'из Москвы'), req('destination', 'AER', 'hard', 'Сочи', 'в Сочи'), req('dates', { from: '2026-11-10', to: '2026-11-17' }, 'hard', '10–17 ноября', '10–17 ноября'), req('travelers', { adults: 2 }, 'hard', '2 взрослых', 'двое взрослых')]
      : [req('destination', 'AER', 'hard', 'Сочи', text)]
    return {
      steps: [step('llm', 'parse_request', { text }), step('tool', 'search_transport', { from: 'MOW', to: 'AER' }, { offers: 14 }), step('tool', 'search_hotels', { city: 'AER', nights: 7 }, { offers: 9 }), step('tool', 'build_bundles', {}, { bundles: 2 }), step('tool', 'validate_plan', {}, { warn: 1, block: 0 })],
      patch: { stage: 'assemble', requirements: upsert(st.requirements, reqs), destinations: [], open_questions: [], proposed_changes: [], bundles: bundlesSochi() },
      reply: 'Собрал два проверенных комплекта на 10–17 ноября: «дешевле» — поездом, «быстрее» — самолётом. В обоих вы будете в отеле раньше заселения — показал варианты.',
    }
  }

  return { steps: [step('llm', 'parse_request', { text }, { requirements: 0 })], patch: {}, reply: 'Расскажите чуть подробнее: куда или какой отдых хочется, когда и кто едет?' }
}

async function emitTurn(s: MockSession, turn: Turn, userText: string | null, onEvent: (e: StreamEvent) => void) {
  for (const st of turn.steps) {
    await sleep(320)
    s.trace.push({ ...st, version: s.state.version + 1, step_no: s.trace.length + 1, created_at: new Date().toISOString() })
    onEvent({ event: 'step', data: st })
  }
  s.state = { ...s.state, ...turn.patch, version: s.state.version + 1 }
  if (userText) s.messages.push({ role: 'user', text: userText })
  s.messages.push({ role: 'assistant', text: turn.reply })
  onEvent({ event: 'state', data: { state: s.state } })
  onEvent({ event: 'message', data: { role: 'assistant', text: turn.reply } })
  onEvent({ event: 'done', data: { ok: true, version: s.state.version } })
}

function emptyState(id: string): TripState {
  return { session_id: id, version: 0, stage: 'clarify', requirements: [], open_questions: [], destinations: [], bundles: [], selected_bundle_id: null, history: [] }
}

function err(status: number, code: string, message: string, extra: Record<string, unknown> = {}): never {
  throw new ApiError(status, { code, message, ...extra })
}

function session(id: string): MockSession {
  return sessions.get(id) ?? err(404, 'session_not_found', 'Сессия не найдена')
}

function checkVersion(s: MockSession, expected: number) {
  if (expected !== s.state.version) err(409, 'version_conflict', 'План изменился', { current_version: s.state.version })
}

export const mockApi: Api = {
  async createSession(): Promise<SessionCreated> {
    const id = uid()
    sessions.set(id, { state: emptyState(id), messages: [], trace: [] })
    return { session_id: id, version: 0 }
  },
  async getSession(id): Promise<SessionView> {
    const s = session(id)
    return { state: s.state, messages: [...s.messages] }
  },
  async getTrace(id) {
    return { session_id: id, steps: [...session(id).trace] }
  },
  async sendMessage(id, body: MessageIn, onEvent) {
    const s = session(id)
    checkVersion(s, body.expected_version)
    await emitTurn(s, think(s, body.text), body.text, onEvent)
  },
  async sendCommand(id, body: CommandIn, onEvent) {
    const s = session(id)
    checkVersion(s, body.expected_version)
    const p = body.payload as Record<string, string>
    let turn: Turn
    switch (body.type) {
      case 'remove_requirement':
        turn = { steps: [step('state', 'remove_requirement', p)], patch: { requirements: s.state.requirements.filter((r) => r.key !== p.key) }, reply: 'Убрал это условие — план перепроверю с учётом изменения.' }
        break
      case 'remove_assumption':
        turn = { steps: [step('state', 'remove_assumption', p)], patch: { requirements: s.state.requirements.filter((r) => r.key !== p.key), suppressed_assumptions: [...(s.state.suppressed_assumptions ?? []), p.key] }, reply: 'Понял, эту догадку больше не учитываю.' }
        break
      case 'edit_requirement':
        turn = { steps: [step('state', 'edit_requirement', p)], patch: { requirements: s.state.requirements.map((r) => (r.key === p.key ? { ...r, value: p.value, kind: (p.kind as Requirement['kind']) ?? 'hard', source: 'user_edit', label: p.label ?? r.label } : r)) }, reply: 'Зафиксировал — теперь это ваше требование, а не моя догадка.' }
        break
      case 'select_destination':
        turn = think(s, 'выбираю Сочи')
        break
      case 'accept_alternative': {
        const r = s.state.proposed_changes?.find((x) => x.id === p.alternative_id)
        turn = { steps: [step('tool', 'accept_alternative', p), step('tool', 'build_bundles', {}, { bundles: 2 })], patch: { proposed_changes: [], stage: 'assemble', bundles: bundlesSochi() }, reply: `Принято: ${r?.label ?? 'изменение'}. Собрал варианты (в моке — на примере Сочи).` }
        break
      }
      default:
        err(422, 'validation', `unsupported command type: ${body.type}`)
    }
    await emitTurn(s, turn, null, onEvent)
  },
  async createCart(body: CartIn): Promise<Cart> {
    await sleep(200)
    const s = session(body.session_id)
    if (s.state.version !== body.version) err(409, 'version_conflict', 'План изменился, обновите страницу', { current_version: s.state.version })
    const bundle = s.state.bundles.find((b) => b.id === body.bundle_id) ?? err(404, 'bundle_not_found', 'Комплект не найден')
    if (bundle.checks.some((c) => c.status === 'block')) err(422, 'plan_blocked', 'В плане есть блокирующие проблемы')
    const cart: Cart = {
      cart_id: uid(), status: 'open', total_rub: bundle.total_rub,
      snapshot: { session_id: body.session_id, version: body.version, bundle: structuredClone(bundle), requirements: structuredClone(s.state.requirements), created_at: new Date().toISOString() },
    }
    carts.set(cart.cart_id, cart)
    return cart
  },
  async getCart(id) {
    return carts.get(id) ?? err(404, 'cart_not_found', 'Корзина не найдена')
  },
  async checkout(cartId, body: CheckoutIn): Promise<Order> {
    await sleep(700)
    const prev = ordersByKey.get(body.idempotency_key)
    if (prev) return { ...orders.get(prev)!, replay: true }
    const cart = carts.get(cartId) ?? err(404, 'cart_not_found', 'Корзина не найдена')
    if (cart.status === 'ordered') return { ...[...orders.values()].find((o) => o.cart_id === cartId)!, replay: true }
    const s = sessions.get(cart.snapshot.session_id)
    if (s && s.state.version !== cart.snapshot.version) err(409, 'plan_changed', 'План изменился после добавления в корзину')
    const now = new Date().toISOString()
    const order: Order = {
      order_id: uid(), cart_id: cartId, status: 'paid', total_rub: cart.total_rub,
      snapshot: { ...cart.snapshot, ordered_at: now }, payer: body.payer,
      payment: { mock: true, method: 'card', ref: 'MOCK-' + Math.random().toString(16).slice(2, 10).toUpperCase(), paid_at: now },
      created_at: now,
    }
    orders.set(order.order_id, order)
    ordersByKey.set(body.idempotency_key, order.order_id)
    cart.status = 'ordered'
    return { ...order, replay: false }
  },
  async getOrder(id) {
    return orders.get(id) ?? err(404, 'order_not_found', 'Заказ не найден')
  },
}
