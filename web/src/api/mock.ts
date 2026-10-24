/**
 * Мок бэкенда для `npm run dev:mock`: агент, корзина и заказы в памяти браузера.
 * Повторяет форму реального API (SSE app/main.py, версии и 409, идемпотентность оплаты)
 * и проигрывает сценарии спринта 2: размытый запрос → уточнения → направления → комплекты;
 * невыполнимый запрос → «вариантов нет, вот минимальные изменения». Данные вымышленные.
 */
import type {
  Bundle, Cart, CartIn, ChatMessage, CheckoutIn, CommandIn, DestinationOption, MessageIn, Offer, Order, ProposedChange,
  Reminder, Requirement, RequestStatus, SessionCreated, SessionView, StreamEvent, TraceStep, TripState,
} from './types'
import type { Api } from './client'
import { ApiError } from './errors'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const uid = () => crypto.randomUUID()

interface MockSession {
  state: TripState
  messages: ChatMessage[]
  trace: TraceStep[]
  opts: TripOpts
}
const sessions = new Map<string, MockSession>()
const carts = new Map<string, Cart>()
const orders = new Map<string, Order>()
const ordersByKey = new Map<string, string>()
/** сессии, где уже сыграли #конфликт */
const conflicted = new Set<string>()

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

interface TripOpts {
  /** сдвиг поездки в днях */
  shift: number
  pets: boolean
  cheaper: boolean
}
const BASE: TripOpts = { shift: 0, pets: false, cheaper: false }

/** ISO-время с поясом Сочи (+03:00) от базовой даты 2026-11-10 со сдвигом */
function at(day: number, hm: string): string {
  const d = new Date(Date.UTC(2026, 10, 10 + day))
  return `${d.toISOString().slice(0, 10)}T${hm}:00+03:00`
}

/** стабильный offer_id из содержания — одинаковое предложение в двух версиях плана = «сохранено» */
function fixed(o: Partial<Offer> & Pick<Offer, 'kind' | 'title' | 'total_rub'>): Offer {
  const key = `${o.kind}_${o.title}_${o.times?.start ?? ''}`.replace(/[^\wа-яё]+/gi, '_').toLowerCase()
  return offer({ ...o, offer_id: `off_${key}` })
}

function bundlesSochi(opt: TripOpts = BASE): Bundle[] {
  const s = opt.shift
  const n = 7
  const hotel = opt.pets
    ? fixed({
        kind: 'hotel', title: 'Морская звезда · Студия, можно с собакой до 15 кг', total_rub: 61600, unit_price_rub: 8800,
        price_unit: 'per_room_night', units: n, included: ['завтрак', 'wi-fi', 'питомец'],
        cancel_policy: { free_until: at(s - 3, '23:59'), penalty_rub: 8800 },
        times: { start: at(s, '14:00'), end: at(s + n, '12:00') },
      })
    : opt.cheaper
      ? fixed({
          kind: 'hotel', title: 'Гостевой дом «Магнолия» · Стандарт', total_rub: 33600, unit_price_rub: 4800,
          price_unit: 'per_room_night', units: n, included: ['wi-fi'],
          cancel_policy: { free_until: at(s - 1, '23:59'), penalty_rub: 4800 },
          times: { start: at(s, '14:00'), end: at(s + n, '12:00') },
        })
      : fixed({
          kind: 'hotel', title: 'Ривьера Парк · Стандарт, завтрак', total_rub: 54600, unit_price_rub: 7800,
          price_unit: 'per_room_night', units: n, included: ['завтрак', 'wi-fi'],
          cancel_policy: { free_until: at(s - 3, '23:59'), penalty_rub: 7800 },
          times: { start: at(s, '14:00'), end: at(s + n, '12:00') },
        })
  const taxi = (title: string, start: string, end: string, price: number) =>
    fixed({ kind: 'transfer', title: opt.pets ? `${title}, с переноской` : title, total_rub: opt.pets ? price + 500 : price, price_unit: 'per_vehicle', times: { start, end } })

  const flightOut = (flex: boolean) =>
    fixed({
      kind: 'flight', title: `SU 1120 · Шереметьево → Сочи${flex ? ', гибкий тариф' : ''}`, total_rub: flex ? 25800 : 19800,
      unit_price_rub: flex ? 12900 : 9900, price_unit: 'per_person', units: 2, baggage: '1 × 23 кг',
      times: { start: at(s, '07:30'), end: at(s, '10:05') },
      cancel_policy: flex ? { free_until: at(s - 1, '07:30'), penalty_rub: 0 } : { free_until: null, penalty_rub: 19800 },
    })
  const flightBack = (flex: boolean) =>
    fixed({
      kind: 'flight', title: `SU 1125 · Сочи → Шереметьево${flex ? ', гибкий тариф' : ''}`, total_rub: flex ? 27400 : 21400,
      unit_price_rub: flex ? 13700 : 10700, price_unit: 'per_person', units: 2, baggage: '1 × 23 кг',
      times: { start: at(s + n, '15:20'), end: at(s + n, '17:55') },
      cancel_policy: flex ? { free_until: at(s + n - 1, '15:20'), penalty_rub: 0 } : { free_until: null, penalty_rub: 21400 },
    })

  const fly = (flex: boolean) => {
    const items = [
      flightOut(flex),
      taxi('Такси · аэропорт → отель', at(s, '10:25'), at(s, '11:05'), 2400),
      hotel,
      taxi('Такси · отель → аэропорт', at(s + n, '12:30'), at(s + n, '13:10'), 2400),
      flightBack(flex),
    ]
    return items
  }
  const rail = [
    fixed({ kind: 'train', title: 'Поезд 104В · Москва → Сочи, купе', total_rub: 14600, unit_price_rub: 7300, price_unit: 'per_person', units: 2, times: { start: at(s - 1, '09:10'), end: at(s, '09:58') } }),
    taxi('Такси · вокзал → отель', at(s, '10:10'), at(s, '10:25'), 900),
    hotel,
    taxi('Такси · отель → вокзал', at(s + n, '12:30'), at(s + n, '12:45'), 900),
    fixed({ kind: 'train', title: 'Поезд 103С · Сочи → Москва, купе', total_rub: 15200, unit_price_rub: 7600, price_unit: 'per_person', units: 2, times: { start: at(s + n, '14:05'), end: at(s + n + 1, '15:20') } }),
  ]

  const early = (items: Offer[], time: string): Bundle['checks'][number] => ({
    kind: 'early_arrival', status: 'warn', text: `В отеле в ${time}, заселение с 14:00`,
    options: ['Оставить багаж на стойке — бесплатно', 'Ранний заезд с 11:00 — 2 500 ₽, не гарантирован'],
    offer_ids: [items[1].offer_id, items[2].offer_id],
  })
  const back = (items: Offer[], text: string): Bundle['checks'][number] => ({
    kind: 'return_trip', status: 'ok', text, offer_ids: [items[2].offer_id, items[3].offer_id, items[4].offer_id],
  })
  const pets = (items: Offer[]): Bundle['checks'][number][] =>
    opt.pets ? [{ kind: 'pets', status: 'ok', text: 'Собаку берут перевозчик, отель и трансфер', offer_ids: items.map((o) => o.offer_id) }] : []
  const budget: Bundle['checks'][number] = { kind: 'budget', status: 'ok', text: 'Укладывается в бюджет' }

  const cheap = rail
  const fast = fly(false)
  const flex = fly(true)
  return [
    {
      id: 'b-cheap', label: 'дешевле', items: cheap, total_rub: sum(cheap),
      checks: [early(cheap, '10:25'), back(cheap, 'Выезд в 12:00, поезд в 14:05 — запас 1 ч 20 мин'), ...pets(cheap), budget],
      tradeoffs: [{ text: 'Сутки в поезде в каждую сторону — минус почти два дня отдыха', refs: ['offer:train'] }],
      risks: [],
    },
    {
      id: 'b-fast', label: 'быстрее', items: fast, total_rub: sum(fast),
      checks: [early(fast, '11:05'), back(fast, 'Выезд в 12:00, вылет в 15:20 — в аэропорту за 2 ч 10 мин'), ...pets(fast), budget],
      tradeoffs: [{ text: `Дороже «дешевле» на ${(sum(fast) - sum(cheap)).toLocaleString('ru-RU')} ₽, зато 2,5 часа в пути вместо суток`, refs: ['offer:flight', 'offer:train'] }],
      risks: [{ text: 'Невозвратный тариф перелёта', refs: ['offer:SU1120.cancel_policy'] }],
    },
    {
      id: 'b-flex', label: 'гибче отмена', items: flex, total_rub: sum(flex),
      checks: [early(flex, '11:05'), back(flex, 'Выезд в 12:00, вылет в 15:20 — в аэропорту за 2 ч 10 мин'), ...pets(flex), budget],
      tradeoffs: [{ text: 'Гибкий тариф дороже на 12 000 ₽ — зато всё можно отменить без штрафа', refs: ['offer:flight'] }],
      risks: [],
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

  // пересборка готового плана при изменении условий
  if (st.bundles.length && /(позже|раньше|собак|кошк|животн|дешевле|бюджет меньше)/.test(t)) {
    const prevOpts = s.opts
    const opts: TripOpts = {
      shift: prevOpts.shift + (/(\d)\s*дн[яей]* позже/.test(t) ? Number(t.match(/(\d)\s*дн/)![1]) : /позже/.test(t) ? 2 : 0),
      pets: prevOpts.pets || /собак|кошк|животн/.test(t),
      cheaper: prevOpts.cheaper || /дешевле|бюджет меньше/.test(t),
    }
    s.opts = opts
    const bundles = bundlesSochi(opts)
    const prevSel = st.bundles.find((b) => b.id === st.selected_bundle_id) ?? st.bundles[0]
    const nextSel = bundles.find((b) => b.id === prevSel.id) ?? bundles[0]
    const what = [opts.shift !== prevOpts.shift && `даты +${opts.shift - prevOpts.shift} дн.`, opts.pets && !prevOpts.pets && 'едет собака', opts.cheaper && !prevOpts.cheaper && 'бюджет меньше'].filter(Boolean).join(', ')
    const reqs = [
      ...(opts.shift !== prevOpts.shift ? [req('dates', { from: at(opts.shift, '00:00').slice(0, 10), to: at(opts.shift + 7, '00:00').slice(0, 10) }, 'hard', `${10 + opts.shift}–${17 + opts.shift} ноября`, 'на два дня позже')] : []),
      ...(opts.pets && !prevOpts.pets ? [req('pets', [{ kind: 'dog', weight_kg: 10 }], 'hard', 'С собакой', 'с собакой')] : []),
    ]
    const replaced = [
      ...(opts.pets && !prevOpts.pets ? ['Ривьера Парк не принимает собак → Морская звезда (до 15 кг)', 'Такси → с переноской для собаки'] : []),
      ...(opts.shift !== prevOpts.shift ? ['Рейсы и поезда — на новые даты'] : []),
      ...(opts.cheaper && !prevOpts.cheaper ? ['Отель → гостевой дом «Магнолия», дорога без изменений'] : []),
    ]
    return {
      steps: [step('llm', 'parse_request', { text }, { changed: what }), step('tool', 'replan', { changed: what }, { recheck: 5 }), step('tool', 'search_transport', {}, { offers: 12 }), step('tool', 'search_hotels', { pets: opts.pets }, { offers: 4 }), step('tool', 'validate_plan', {}, { block: 0 })],
      patch: {
        stage: 'assemble', requirements: upsert(st.requirements, reqs), bundles, selected_bundle_id: st.selected_bundle_id,
        history: [...st.history, { version: st.version + 1, change: what.charAt(0).toUpperCase() + what.slice(1), kept: [], replaced, price_delta_rub: nextSel.total_rub - prevSel.total_rub }],
      },
      reply: `Пересобрал план (${what}): перепроверил дорогу, трансферы и отель, заменил только то, что перестало подходить. Разница видна в блоке «Агент пересобрал план».`,
    }
  }

  // выбор направления или конкретный запрос → комплекты
  if (/сочи|анталь|шарм|выбира/.test(t) || /из\s/.test(t)) {
    const reqs = /из москв/.test(t)
      ? [req('origin', 'Москва', 'hard', 'Из Москвы', 'из Москвы'), req('destination', 'AER', 'hard', 'Сочи', 'в Сочи'), req('dates', { from: '2026-11-10', to: '2026-11-17' }, 'hard', '10–17 ноября', '10–17 ноября'), req('travelers', { adults: 2 }, 'hard', '2 взрослых', 'двое взрослых')]
      : [req('destination', 'AER', 'hard', 'Сочи', text)]
    return {
      steps: [step('llm', 'parse_request', { text }), step('tool', 'search_transport', { from: 'MOW', to: 'AER' }, { offers: 14 }), step('tool', 'search_hotels', { city: 'AER', nights: 7 }, { offers: 9 }), step('tool', 'build_bundles', {}, { bundles: 2 }), step('tool', 'validate_plan', {}, { warn: 1, block: 0 })],
      patch: { stage: 'assemble', requirements: upsert(st.requirements, reqs), destinations: [], open_questions: [], proposed_changes: [], bundles: bundlesSochi(s.opts) },
      reply: 'Собрал три проверенных комплекта на 10–17 ноября: «дешевле» — поездом, «быстрее» и «гибче отмена» — самолётом. Сравнил их по вашим критериям; везде вы будете в отеле раньше заселения — показал варианты.',
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
  async getStatus() {
    return { llm: { configured: false }, database: true, agent_mode: 'llm', message: null }
  },
  async createSession(): Promise<SessionCreated> {
    const id = uid()
    sessions.set(id, { state: emptyState(id), messages: [], trace: [], opts: { ...BASE } })
    return { session_id: id, version: 0 }
  },
  async getSession(id): Promise<SessionView> {
    const s = session(id)
    return { state: s.state, messages: [...s.messages] }
  },
  async getRequestStatus(id, requestId): Promise<RequestStatus> {
    return { request_id: requestId, status: 'not_found', current_version: session(id).state.version, replay_available: false }
  },
  async getTrace(id) {
    return { session_id: id, steps: [...session(id).trace] }
  },
  async sendMessage(id, body: MessageIn, onEvent) {
    const s = session(id)
    // сбои для проверки UI (спринт 4, C1/C2): #конфликт — план «поменяли в другой вкладке» (409 один раз),
    // #ошибка — LLM упала (SSE error), #обрыв — поток закончился без done
    if (body.text.includes('#конфликт') && !conflicted.has(id)) {
      conflicted.add(id)
      s.state = { ...s.state, version: s.state.version + 1 }
    }
    checkVersion(s, body.expected_version)
    if (body.text.includes('#ошибка')) {
      await sleep(400)
      onEvent({ event: 'error', data: { code: 'llm_request_failed', message: 'mock: LLM timeout' } })
      onEvent({ event: 'done', data: { ok: false } })
      return
    }
    if (body.text.includes('#обрыв')) {
      await sleep(400)
      return
    }
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
      case 'select_bundle': {
        const b = s.state.bundles.find((x) => x.id === p.bundle_id)
        if (!b) err(422, 'invalid_command', 'bundle_id must identify a current bundle')
        turn = { steps: [step('state', 'select_bundle', p)], patch: { selected_bundle_id: b.id, stage: 'assemble' }, reply: `Отлично, берём «${b.label}». Таймлайн поездки и проверки стыковок — ниже; можно оформлять.` }
        break
      }
      case 'select_destination':
        turn = think(s, 'выбираю Сочи')
        break
      case 'accept_alternative': {
        const r = s.state.proposed_changes?.find((x) => x.id === p.alternative_id)
        turn = { steps: [step('tool', 'accept_alternative', p), step('tool', 'build_bundles', {}, { bundles: 2 })], patch: { proposed_changes: [], stage: 'assemble', bundles: bundlesSochi(s.opts) }, reply: `Принято: ${r?.label ?? 'изменение'}. Собрал варианты (в моке — на примере Сочи).` }
        break
      }
      case 'select_bundle': {
        const selected = s.state.bundles.find((b) => b.id === p.bundle_id)
        if (!selected) err(404, 'bundle_not_found', 'Комплект не найден в текущем плане')
        turn = { steps: [step('state', 'select_bundle', p)], patch: { selected_bundle_id: p.bundle_id, stage: 'assemble' }, reply: 'Комплект выбран для сборки.' }
        break
      }
      case 'request_checkout': {
        const selected = s.state.bundles.find((b) => b.id === s.state.selected_bundle_id)
        if (!selected) err(422, 'invalid_command', 'Сначала выберите комплект')
        const blocked = selected.checks.some((c) => c.status === 'block')
        turn = { steps: [step('tool', 'validate_plan', { bundle_id: selected.id }, { checks: selected.checks })],
          patch: { stage: blocked ? 'assemble' : 'checkout' },
          reply: blocked ? 'В плане есть блокирующая проблема.' : 'Проверки демо-комплекта не содержат блокировок.' }
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
  // после покупки: упрощённо, только чтобы страница заказа рисовалась без бэкенда
  async getAftercare(id) {
    const o = orders.get(id) ?? err(404, 'order_not_found', 'Заказ не найден')
    const b = o.snapshot.bundle
    const now = Date.now()
    return {
      order_id: id, bundle: b, total_rub: b.total_rub,
      reminders: b.items.filter((x) => x.times).map((x) => ({
        at: x.times!.start, kind: x.kind === 'hotel' ? 'checkin' : x.kind === 'transfer' ? 'transfer' : 'departure',
        text: x.title ?? x.item_id, offer_id: x.offer_id, past: Date.parse(x.times!.start) < now,
      })) as Reminder[],
      cancellation: { items: b.items.map((x) => ({ offer_id: x.offer_id, kind: x.kind, title: x.title ?? x.item_id, total_rub: x.total_rub,
        status: 'penalty' as const, penalty_rub: x.cancel_policy?.penalty_rub ?? 0, free_until: x.cancel_policy?.free_until ?? null })),
        penalty_total_rub: b.items.reduce((s, x) => s + (x.cancel_policy?.penalty_rub ?? 0), 0) },
      early_checkin: { hotel: 'Отель', available: true, from: '10:00', price_rub: 2500, guaranteed: false, standard_checkin: '14:00', arrival_at: null, source_refs: [] },
      changes: [],
    }
  },
  async quoteDateShift(id, days) {
    const o = orders.get(id) ?? err(404, 'order_not_found', 'Заказ не найден')
    return { order_id: id, days, feasible: true, old_dates: { start: '2026-11-10', end: '2026-11-17' },
      new_dates: { start: '2026-11-12', end: '2026-11-19' }, kept: [], replaced: o.snapshot.bundle.items.map((x) => ({ offer_id: x.offer_id, title: x.title ?? '' })),
      added: [], penalties: [], price_delta_rub: 1200, penalty_total_rub: 0, to_pay_rub: 1200, old_total_rub: o.total_rub, new_total_rub: o.total_rub + 1200 }
  },
  async confirmDateShift(id) {
    return { change_id: uid(), kind: 'date_shift', status: 'confirmed', quote: { order_id: id }, created_at: new Date().toISOString(), replay: false }
  },
  async requestEarlyCheckin(id) {
    return { change_id: uid(), kind: 'early_checkin', status: 'confirmed', quote: { order_id: id, status_text: 'запрошен' }, created_at: new Date().toISOString(), replay: false }
  },
}
