/**
 * Типы API — КОНТРАКТ фронта с бэкендом (plan/00-architecture.md, п.4, 6, 7, 10).
 * Меняешь форму — обнови архитектуру и предупреди команду.
 * Даты/время — ISO 8601 с часовым поясом. Деньги — целые рубли.
 */

// ---------- предложения (п.4) ----------

export type OfferKind = 'hotel' | 'flight' | 'train' | 'transfer'
export type PriceUnit = 'per_room_night' | 'per_person' | 'per_vehicle' | 'per_trip'

export interface Offer {
  offer_id: string
  kind: OfferKind
  item_id: string
  /** человекочитаемое название — в бэкенде пока нет (Offer extra=forbid), фронт собирает из variant/item_id */
  title?: string
  variant?: Record<string, string>
  capacity?: { adults?: number; children?: number; pets?: boolean; luggage?: number }
  quantity: number
  unit_price_rub: number
  price_unit: PriceUnit
  units: number
  /** итог позиции на весь состав и весь период — только он идёт в сумму */
  total_rub: number
  included?: string[]
  baggage?: string | null
  cancel_policy?: { free_until: string | null; penalty_rub: number } | null
  times?: { start: string; end: string }
  /** условия сгенерированы — показываем метку на карточке */
  synthetic: boolean
  source_refs?: string[]
}

// ---------- требования (п.5) ----------

export type RequirementKind = 'hard' | 'soft' | 'assumption' | 'unknown'

export interface Requirement {
  key: string
  value: unknown
  kind: RequirementKind
  source?: 'user_text' | 'user_edit' | 'agent' | 'memory'
  /** цитата из сообщения пользователя, откуда взято */
  quote?: string
  /** подпись для UI: «Бюджет до 100 000 ₽» */
  label?: string
}

export interface OpenQuestion {
  key: string
  question: string
  why: string
  /** быстрые ответы кнопками (спринт 2, B); нет — только свободный текст */
  options?: string[]
}

// ---------- объяснения, проверки, комплекты (п.6, 7) ----------

export interface Claim {
  text: string
  /** ссылки на данные: «climate:AYT:11.sea_temp», «offer:off_...» */
  refs: string[]
}

export type CheckStatus = 'ok' | 'warn' | 'block'

export interface Check {
  id?: string
  kind: string
  status: CheckStatus
  text: string
  options?: string[]
  offer_ids?: string[]
  refs?: string[]
}

export interface Bundle {
  id: string
  label: string
  items: Offer[]
  total_rub: number
  checks: Check[]
  advantages?: Claim[]
  tradeoffs: Claim[]
  risks: Claim[]
}

/**
 * Направление-кандидат: A app/planning/suggest.py → B state.destinations (только feasible, до 3 шт.).
 * Обязательны id и name; остальное фронт показывает, если пришло.
 */
export interface DestinationOption {
  id: string // IATA города: AYT, SSH, DXB
  name: string
  country?: string
  feasible?: boolean
  /** оценка поездки целиком на весь состав, ₽ */
  est_total_rub?: number
  /** погода в месяц поездки */
  climate?: { month: number; air_temp_day?: number; air_temp_night?: number; sea_temp?: number | null; rainy_days?: number; sun_hours?: number }
  /** дорога в одну сторону */
  travel?: { kind: 'flight' | 'train'; duration_min: number; stops?: number; title?: string }
  visa?: { regime: 'domestic' | 'visa_free' | 'visa_on_arrival' | 'evisa' | 'visa_required'; note?: string }
  nights?: number
  dates?: { start: string; end: string }
  /** предположения A при подборе: «без долгих перелётов» = до 5 ч */
  assumptions?: string[]
  tags?: string[]
  why?: Claim[]
  risks?: Claim[]
  rank?: number
  [k: string]: unknown
}

/**
 * «Под все условия вариантов нет — вот минимальные изменения»: A app/planning/suggest.py → B state.proposed_changes.
 * Жёсткие требования сами не ослабляются: пользователь принимает изменение командой accept_alternative.
 */
export interface ProposedChange {
  id: string // alt-budget-1
  /** «Другое направление — Сочи — 53 786 ₽ (+23 786 ₽ к бюджету)» */
  label: string
  kind: string
  total_rub?: number
  /** на сколько выходит за бюджет; 0/null — укладывается */
  over_budget_rub?: number | null
  /** изменение ослабляет обязательное условие — нужно явное согласие */
  needs_consent?: boolean
  changes?: { key: string; from?: unknown; to?: unknown; to_name?: string }[]
  refs?: string[]
  [k: string]: unknown
}

export type Stage = 'clarify' | 'inspire' | 'select' | 'compare' | 'assemble' | 'checkout' | 'booked'

export interface HistoryEntry {
  version: number
  change: string
  kept: string[]
  replaced: string[]
  price_delta_rub: number | null
  reasons?: string[]
  item_reasons?: Record<string, string>
  previous_bundle?: Bundle | null
  resulting_bundle_id?: string | null
}

export interface TripState {
  session_id: string
  version: number
  stage: Stage
  requirements: Requirement[]
  open_questions: OpenQuestion[]
  destinations: DestinationOption[]
  bundles: Bundle[]
  selected_bundle_id: string | null
  pending_replan?: { status: 'stale_source'; source_bundle: Bundle; previous_requirements: Requirement[]; change: string } | null
  history: HistoryEntry[]
  /** честный ответ на невыполнимый запрос: подтверждённые минимальные изменения */
  proposed_changes?: ProposedChange[]
  /** догадки, которые пользователь убрал — агент их больше не подставляет */
  suppressed_assumptions?: string[]
  selected_destination_id?: string | null
}

// ---------- сессии и поток событий (п.10) ----------

export interface ChatMessage {
  id?: string
  role: 'user' | 'assistant' | 'system'
  text: string
  created_at?: string
}

export interface SessionCreated {
  session_id: string
  version: number
}

export interface SessionView {
  state: TripState
  messages: ChatMessage[]
}

export interface RequestStatus {
  request_id: string
  status: 'not_found' | 'processing' | 'completed' | 'failed'
  committed_version?: number | null
  current_version: number
  error?: { code: string; message: string } | null
  replay_available: false
}

/** Шаг трейса — реальные входы/выходы инструментов, не «мысли» модели */
export interface TraceStep {
  step_type: 'llm' | 'tool' | 'state' | string
  name: string
  input?: unknown
  output?: unknown
  status: 'ok' | 'error' | string
  /** есть в GET /trace */
  version?: number
  step_no?: number
  created_at?: string
}

/** GET /api/sessions/{id}/trace */
export interface TraceView {
  session_id: string
  steps: TraceStep[]
}

/**
 * SSE: POST /api/sessions/{id}/messages (формат app/main.py, трек B).
 * 409 при устаревшей expected_version приходит ДО потока как обычный HTTP-ответ.
 */
export type StreamEvent =
  | { event: 'step'; data: TraceStep }
  | { event: 'state'; data: { state: TripState } }
  | { event: 'message'; data: { role: 'assistant'; text: string } }
  | { event: 'replay'; data: { request_id: string; committed_version?: number; current_version: number; reload: true } }
  | { event: 'error'; data: { code: string; message: string; current_version?: number } }
  | { event: 'done'; data: { ok: boolean; version?: number } }

export interface MessageIn {
  request_id: string
  text: string
  expected_version: number
}

/** POST /api/sessions/{id}/commands — список и payload как в CommandRequest (app/main.py) */
export interface CommandIn {
  request_id: string
  type:
    | 'edit_requirement' // {key, value, kind?: hard|soft, label?}
    | 'remove_requirement' // {key}
    | 'remove_assumption' // {key}
    | 'select_destination' // {destination_id}
    | 'accept_alternative' // {alternative_id}
    | 'select_bundle' // {bundle_id}
    | 'request_checkout' // {} — validate selected bundle before starting checkout; cart still validates again
  payload: Record<string, unknown>
  expected_version: number
}

// ---------- корзина и заказы ----------

export interface CartIn {
  session_id: string
  version: number
  bundle_id: string
}

export interface CartSnapshot {
  session_id: string
  version: number
  bundle: Bundle
  requirements: Requirement[]
  created_at: string
}

export interface Cart {
  cart_id: string
  status: 'open' | 'ordered'
  total_rub: number
  snapshot: CartSnapshot
  created_at?: string
}

export interface Payer {
  name: string
  email: string
}

export interface CheckoutIn {
  payer: Payer
  idempotency_key: string
}

export interface Order {
  order_id: string
  cart_id: string
  status: 'paid' | 'cancelled'
  total_rub: number
  snapshot: CartSnapshot & { ordered_at: string }
  payer: Payer
  payment: { mock: true; method: string; ref: string; paid_at: string }
  created_at: string
  replay?: boolean
}

// ---------- после покупки (app/cart/aftercare.py) ----------

export interface Reminder {
  at: string
  kind: 'checkin_online' | 'departure' | 'transfer' | 'checkin' | 'checkout' | 'free_cancel_deadline'
  text: string
  offer_id: string
  offer_kind?: OfferKind
  past: boolean
}

export interface CancelTerms {
  status: 'free' | 'penalty' | 'nonrefundable'
  penalty_rub: number
  free_until: string | null
}

export interface EarlyCheckinOption {
  hotel: string
  available: boolean
  from: string | null
  price_rub: number
  guaranteed: boolean
  standard_checkin: string
  luggage_storage?: boolean
  arrival_at: string | null
  source_refs: string[]
}

export interface OrderChange {
  change_id: string
  kind: 'date_shift' | 'early_checkin'
  status: string
  quote: Record<string, any>
  created_at: string
}

export interface Aftercare {
  order_id: string
  bundle: Bundle
  total_rub: number
  reminders: Reminder[]
  cancellation: { items: ({ offer_id: string; kind: OfferKind; title: string; total_rub: number } & CancelTerms)[]; penalty_total_rub: number }
  early_checkin: EarlyCheckinOption | null
  changes: OrderChange[]
}

export interface ShiftItem {
  offer_id: string
  title: string
  kind?: OfferKind
  same_item?: boolean
  total_rub?: number
}

export interface DateShiftQuote {
  order_id: string
  days: number
  feasible: boolean
  old_dates: { start: string; end: string }
  new_dates: { start: string; end: string }
  notes?: string[]
  change?: string
  kept?: ShiftItem[]
  replaced?: ShiftItem[]
  added?: ShiftItem[]
  penalties?: (ShiftItem & CancelTerms)[]
  old_total_rub?: number
  new_total_rub?: number
  price_delta_rub?: number
  penalty_total_rub?: number
  to_pay_rub?: number
}

/** GET /api/status — что работает прямо сейчас */
export interface AppStatus {
  llm: { configured: boolean; provider_host?: string | null; model?: string }
  database: boolean
  /** rules — LLM не настроен, запрос разбирают правила; всё остальное работает */
  agent_mode: 'llm' | 'rules'
  message: string | null
}

/** Ошибки API: {detail: {code, message, ...}} */
export interface ApiErrorDetail {
  code: string
  message: string
  [k: string]: unknown
}
