/**
 * Мок бэкенда для `npm run dev:mock`: агент, корзина и заказы в памяти браузера.
 * Повторяет поведение реального API (версии, 409, идемпотентность оплаты), чтобы фронт был готов к нему.
 * Данные вымышленные.
 */
import type {
  Bundle, Cart, CartIn, CheckoutIn, CommandIn, MessageIn, Offer, Order, Requirement, SessionCreated, SessionView,
  StreamEvent, TripState, ChatMessage,
} from './types'
import type { Api } from './client'
import { ApiError } from './errors'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const uid = () => crypto.randomUUID()

interface MockSession {
  state: TripState
  messages: ChatMessage[]
}
const sessions = new Map<string, MockSession>()
const carts = new Map<string, Cart>()
const orders = new Map<string, Order>()
const ordersByKey = new Map<string, string>()

function offer(o: Partial<Offer> & Pick<Offer, 'kind' | 'title' | 'total_rub'>): Offer {
  return {
    offer_id: 'off_' + Math.random().toString(36).slice(2, 8),
    item_id: o.kind,
    quantity: 1,
    unit_price_rub: o.total_rub,
    price_unit: 'per_trip',
    units: 1,
    synthetic: true,
    ...o,
  }
}

function bundles(): Bundle[] {
  const toHotel = offer({
    kind: 'transfer', title: 'Такси: аэропорт Сочи → отель', total_rub: 2400, price_unit: 'per_vehicle',
    capacity: { adults: 4, luggage: 3 }, times: { start: '2026-11-10T10:25:00+03:00', end: '2026-11-10T11:05:00+03:00' },
  })
  const hotel = offer({
    kind: 'hotel', title: 'Отель «Ривьера Парк», Стандарт, завтрак', total_rub: 54600, unit_price_rub: 7800,
    price_unit: 'per_room_night', units: 7, included: ['завтрак', 'wi-fi'], capacity: { adults: 2 },
    cancel_policy: { free_until: '2026-11-07T23:59:00+03:00', penalty_rub: 7800 },
    times: { start: '2026-11-10T14:00:00+03:00', end: '2026-11-17T12:00:00+03:00' },
  })
  const fromHotel = offer({
    kind: 'transfer', title: 'Такси: отель → аэропорт Сочи', total_rub: 2400, price_unit: 'per_vehicle',
    times: { start: '2026-11-17T12:30:00+03:00', end: '2026-11-17T13:10:00+03:00' },
  })
  const fast: Offer[] = [
    offer({
      kind: 'flight', title: 'SU 1120 Москва (SVO) → Сочи (AER)', total_rub: 19800, unit_price_rub: 9900,
      price_unit: 'per_person', units: 2, baggage: '1 × 23 кг', times: { start: '2026-11-10T07:30:00+03:00', end: '2026-11-10T10:05:00+03:00' },
      cancel_policy: { free_until: null, penalty_rub: 19800 },
    }),
    toHotel, hotel, fromHotel,
    offer({
      kind: 'flight', title: 'SU 1125 Сочи (AER) → Москва (SVO)', total_rub: 21400, unit_price_rub: 10700,
      price_unit: 'per_person', units: 2, baggage: '1 × 23 кг', times: { start: '2026-11-17T15:20:00+03:00', end: '2026-11-17T17:55:00+03:00' },
    }),
  ]
  const cheap: Offer[] = [
    offer({
      kind: 'train', title: 'Поезд 104В Москва → Сочи, купе', total_rub: 14600, unit_price_rub: 7300,
      price_unit: 'per_person', units: 2, times: { start: '2026-11-09T09:10:00+03:00', end: '2026-11-10T09:58:00+03:00' },
    }),
    offer({ ...toHotel, offer_id: 'off_tr1', title: 'Такси: вокзал Сочи → отель', total_rub: 900, times: { start: '2026-11-10T10:10:00+03:00', end: '2026-11-10T10:25:00+03:00' } }),
    hotel,
    offer({ ...fromHotel, offer_id: 'off_tr2', title: 'Такси: отель → вокзал Сочи', total_rub: 900, times: { start: '2026-11-17T12:30:00+03:00', end: '2026-11-17T12:45:00+03:00' } }),
    offer({
      kind: 'train', title: 'Поезд 103С Сочи → Москва, купе', total_rub: 15200, unit_price_rub: 7600,
      price_unit: 'per_person', units: 2, times: { start: '2026-11-17T14:05:00+03:00', end: '2026-11-18T15:20:00+03:00' },
    }),
  ]
  const sum = (xs: Offer[]) => xs.reduce((s, x) => s + x.total_rub, 0)
  return [
    {
      id: 'b-cheap', label: 'дешевле', items: cheap, total_rub: sum(cheap),
      checks: [
        { kind: 'arrival_vs_checkin', status: 'warn', text: 'В отеле в 10:25, заселение с 14:00', options: ['Оставить багаж на стойке (бесплатно)', 'Ранний заезд: доступен с 11:00 за 2 500 ₽, не гарантирован'] },
        { kind: 'return_trip', status: 'ok', text: 'Выезд 12:00, поезд 14:05 — запас 1 ч 20 мин после дороги' },
        { kind: 'budget', status: 'ok', text: 'Укладывается в бюджет' },
      ],
      tradeoffs: [{ text: 'Сутки в поезде в каждую сторону — минус почти 2 дня отдыха', refs: ['offer:train'] }],
      risks: [],
    },
    {
      id: 'b-fast', label: 'быстрее', items: fast, total_rub: sum(fast),
      checks: [
        { kind: 'arrival_vs_checkin', status: 'warn', text: 'В отеле в 11:05, заселение с 14:00', options: ['Оставить багаж на стойке (бесплатно)', 'Ранний заезд: доступен с 11:00 за 2 500 ₽, не гарантирован'] },
        { kind: 'return_trip', status: 'ok', text: 'Выезд 12:00, вылет 15:20 — в аэропорту за 2 ч 10 мин' },
        { kind: 'budget', status: 'ok', text: 'Укладывается в бюджет' },
      ],
      tradeoffs: [{ text: 'Дороже на ~13 000 ₽, зато 2,5 ч в пути вместо суток', refs: ['offer:flight', 'offer:train'] }],
      risks: [{ text: 'Невозвратный тариф перелёта', refs: ['offer:SU1120.cancel_policy'] }],
    },
  ]
}

function parse(text: string): Requirement[] {
  const t = text.toLowerCase()
  const req: Requirement[] = []
  const city = /из москв/.test(t) ? 'Москва' : /из (спб|питер|санкт)/.test(t) ? 'Санкт-Петербург' : null
  if (city) req.push({ key: 'origin', value: city, kind: 'hard', source: 'user_text', quote: `из ${city}`, label: `Откуда: ${city}` })
  if (/сочи/.test(t)) req.push({ key: 'destination', value: 'Сочи', kind: 'hard', source: 'user_text', quote: 'в Сочи', label: 'Куда: Сочи' })
  const dates = t.match(/(\d{1,2})\s*[–-]\s*(\d{1,2})\s*ноябр/)
  if (dates) req.push({ key: 'dates', value: { from: `2026-11-${dates[1].padStart(2, '0')}`, to: `2026-11-${dates[2].padStart(2, '0')}` }, kind: 'hard', source: 'user_text', quote: dates[0], label: `Даты: ${dates[1]}–${dates[2]} ноября` })
  const adults = /(двое|два|2)\s*взросл/.test(t) ? 2 : null
  req.push(adults
    ? { key: 'travelers', value: { adults: 2 }, kind: 'hard', source: 'user_text', quote: 'двое взрослых', label: '2 взрослых' }
    : { key: 'travelers', value: { adults: 2 }, kind: 'assumption', source: 'agent', label: 'Предположил: 2 взрослых' })
  const budget = t.match(/до\s*(\d+)\s*(тыс|000)/)
  if (budget) req.push({ key: 'budget_total_rub', value: Number(budget[1]) * 1000, kind: /строго/.test(t) ? 'hard' : 'soft', source: 'user_text', quote: budget[0], label: `Бюджет до ${budget[1]} 000 ₽` })
  req.push({ key: 'flight_time', value: 'daytime', kind: 'assumption', source: 'agent', label: 'Предположил: удобное дневное время в пути' })
  return req
}

async function agentTurn(s: MockSession, text: string, onEvent: (e: StreamEvent) => void) {
  s.messages.push({ role: 'user', text })
  const steps = [
    { step_type: 'llm', name: 'parse_request', input: { text }, status: 'ok' },
    { step_type: 'tool', name: 'search_transport', input: { from: 'MOW', to: 'AER' }, status: 'ok' },
    { step_type: 'tool', name: 'search_hotels', input: { city: 'Сочи', nights: 7 }, status: 'ok' },
    { step_type: 'tool', name: 'build_bundles', status: 'ok' },
    { step_type: 'tool', name: 'validate_plan', status: 'ok' },
  ]
  for (const st of steps) {
    onEvent({ event: 'step', data: st })
    await sleep(250)
  }
  s.state = {
    ...s.state,
    version: s.state.version + 1,
    stage: 'assemble',
    requirements: parse(text),
    open_questions: [],
    bundles: bundles(),
    selected_bundle_id: null,
  }
  onEvent({ event: 'state', data: { state: s.state } })
  const reply =
    'Собрал два проверенных комплекта на 10–17 ноября. «Дешевле» — поездом, «быстрее» — самолётом. ' +
    'В обоих вы будете в отеле раньше заселения — показал варианты. Условия отеля и тарифы сгенерированы (демо).'
  onEvent({ event: 'message', data: { role: 'assistant', text: reply } })
  s.messages.push({ role: 'assistant', text: reply })
  onEvent({ event: 'done', data: { ok: true, version: s.state.version } })
}

function emptyState(id: string): TripState {
  return { session_id: id, version: 0, stage: 'clarify', requirements: [], open_questions: [], destinations: [], bundles: [], selected_bundle_id: null, history: [] }
}

function err(status: number, code: string, message: string, extra: Record<string, unknown> = {}): never {
  throw new ApiError(status, { code, message, ...extra })
}

export const mockApi: Api = {
  async createSession(): Promise<SessionCreated> {
    const id = uid()
    sessions.set(id, { state: emptyState(id), messages: [] })
    return { session_id: id, version: 0 }
  },
  async getSession(id): Promise<SessionView> {
    const s = sessions.get(id) ?? err(404, 'session_not_found', 'Сессия не найдена')
    return { state: s.state, messages: [...s.messages] }
  },
  async sendMessage(id, body: MessageIn, onEvent) {
    const s = sessions.get(id) ?? err(404, 'session_not_found', 'Сессия не найдена')
    if (body.expected_version !== s.state.version) err(409, 'version_conflict', 'План изменился', { current_version: s.state.version })
    await agentTurn(s, body.text, onEvent)
  },
  async sendCommand(id, body: CommandIn, onEvent) {
    const s = sessions.get(id) ?? err(404, 'session_not_found', 'Сессия не найдена')
    if (body.expected_version !== s.state.version) err(409, 'version_conflict', 'План изменился', { current_version: s.state.version })
    if (body.type === 'remove_requirement') {
      s.state = { ...s.state, version: s.state.version + 1, requirements: s.state.requirements.filter((r) => r.key !== body.payload.key) }
    }
    onEvent({ event: 'state', data: { state: s.state } })
    onEvent({ event: 'done', data: { ok: true, version: s.state.version } })
  },
  async createCart(body: CartIn): Promise<Cart> {
    await sleep(200)
    const s = sessions.get(body.session_id) ?? err(404, 'session_not_found', 'Сессия не найдена')
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
    await sleep(600)
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
