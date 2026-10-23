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
  tradeoffs: Claim[]
  risks: Claim[]
}

/**
 * Направление-кандидат (спринт 2, A suggest_destinations → B state.destinations).
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
  climate?: { month: number; air_temp_day?: number; sea_temp?: number | null; rainy_days?: number }
  /** дорога в одну сторону */
  travel?: { kind: 'flight' | 'train'; minutes: number; direct?: boolean }
  visa?: { regime: 'domestic' | 'visa_free' | 'visa_on_arrival' | 'evisa' | 'visa_required'; note?: string }
  tags?: string[]
  why?: Claim[]
  risks?: Claim[]
  [k: string]: unknown
}

/**
 * «Под все условия вариантов нет — вот минимальные изменения» (спринт 2, A3).
 * Жёсткие требования сами не ослабляются: пользователь выбирает изменение кнопкой.
 */
export interface Relaxation {
  id: string
  /** что меняем: «Бюджет до 360 000 ₽», «Вылет 13 ноября» */
  text: string
  /** какое требование трогаем */
  key: string
  value?: unknown
  est_total_rub?: number
  refs?: string[]
}

export type Stage = 'clarify' | 'inspire' | 'select' | 'compare' | 'assemble' | 'checkout' | 'booked'

export interface HistoryEntry {
  version: number
  change: string
  kept: string[]
  replaced: string[]
  price_delta_rub: number | null
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
  history: HistoryEntry[]
  /** честный ответ на невыполнимый запрос (спринт 2) */
  relaxations?: Relaxation[]
  /** причины, почему ничего не собралось (A explain_no_bundles) */
  no_options_reasons?: string[]
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
  | { event: 'error'; data: { code: string; message: string; current_version?: number } }
  | { event: 'done'; data: { ok: boolean; version?: number } }

export interface MessageIn {
  text: string
  expected_version: number
}

export interface CommandIn {
  type:
    | 'edit_requirement' // {key, value}
    | 'remove_requirement' // {key}
    | 'confirm_requirement' // {key} — предположение → hard
    | 'answer_question' // {key, value}
    | 'select_destination' // {destination_id}
    | 'apply_relaxation' // {relaxation_id}
    | 'select_bundle'
    | 'replace_item'
    | string
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

/** Ошибки API: {detail: {code, message, ...}} */
export interface ApiErrorDetail {
  code: string
  message: string
  [k: string]: unknown
}
