import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { profileId } from '../lib/profile'
import Icon, { type IconName } from './Icon'

/** что агент помнит — короткими словами для приветствия */
function memoryWords(prefs: Record<string, unknown>): string[] {
  const out: string[] = []
  if (prefs.origin) out.push(`вылет из ${prefs.origin}`)
  if (prefs.adults) out.push(`${prefs.adults} взр.`)
  if (Array.isArray(prefs.children_ages) && prefs.children_ages.length) out.push(`дети ${prefs.children_ages.join(', ')} лет`)
  else if (prefs.children_count) out.push('с ребёнком')
  if (prefs.pets) out.push((prefs.pets as { kind?: string }).kind === 'dog' ? 'с собакой' : 'с питомцем')
  if (prefs.meal_preference === 'all_inclusive') out.push('всё включено')
  if (prefs.transport === 'train') out.push('поездом')
  if (prefs.budget_preference) out.push('подешевле')
  return out
}

/** Стартовый экран в стиле ostrovok.ru: салатовый герой, большая строка запроса, карточки сценариев. */

const MODES: { icon: IconName; label: string; hint: string }[] = [
  { icon: 'sparkle', label: 'Поездка целиком', hint: 'Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов' },
  { icon: 'train', label: 'Поездом', hint: 'Из Москвы в Казань на выходные поездом, двое взрослых' },
  { icon: 'plane', label: 'Точный маршрут', hint: 'Из Москвы в Сочи 10–17 ноября, двое взрослых' },
]

const EXAMPLES: { tag: string; title: string; text: string; tint: string; emoji: string }[] = [
  { tag: 'не знаю, куда', title: 'К тёплому морю с ребёнком', emoji: '🌊', tint: 'from-[#0a4fec] to-[#4225f2]',
    text: 'Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов' },
  { tag: 'с питомцем', title: 'Сочи с собакой', emoji: '🐕', tint: 'from-[#53af24] to-[#2f8a0e]',
    text: 'Из Москвы в Сочи 10–17 ноября, двое взрослых и собака 8 кг' },
  { tag: 'поездом', title: 'Казань на выходные', emoji: '🚆', tint: 'from-[#e77d00] to-[#c75400]',
    text: 'Из Москвы в Казань на выходные поездом, двое взрослых' },
  { tag: 'честный отказ', title: 'Дубай за 30 000 на троих', emoji: '🏙️', tint: 'from-[#ce2121] to-[#9b1111]',
    text: 'Неделя в Дубае на троих за 30 000' },
  { tag: 'english', title: 'Istanbul for two', emoji: '🌍', tint: 'from-[#2d3137] to-[#464a4f]',
    text: 'Trip from Saint Petersburg to Istanbul, November 10-14, two adults' },
]

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: 'sparkle', title: 'Поймёт с полуслова', text: 'Отделит ваши требования от своих догадок и спросит только то, без чего нельзя.' },
  { icon: 'sun', title: 'Подскажет, куда', text: 'Погода, цена поездки целиком, время в пути и виза — по каждому направлению.' },
  { icon: 'shield', title: 'Проверит стыковки', text: 'Ночной прилёт и стойка отеля, трансфер, животное у перевозчика — до оплаты.' },
  { icon: 'refresh', title: 'Пересоберёт, а не начнёт заново', text: '«На два дня позже, с собакой» — покажет, что сохранил, что заменил и почему.' },
]

interface Props {
  disabled: boolean
  connecting: boolean
  onSend: (text: string) => void
}

export default function Landing({ disabled, connecting, onSend }: Props) {
  const qc = useQueryClient()
  const pid = profileId()
  const { data: profile } = useQuery({ queryKey: ['profile', pid], queryFn: () => api.getProfile(pid!), enabled: !!pid, retry: false })
  const remembered = profile ? memoryWords(profile.prefs) : []
  const forget = async () => {
    if (!pid) return
    await api.forgetProfile(pid).catch(() => undefined)
    qc.invalidateQueries({ queryKey: ['profile', pid] })
  }
  const [text, setText] = useState('')
  const [mode, setMode] = useState(0)
  const submit = (t: string) => {
    const v = t.trim()
    if (!v || disabled) return
    onSend(v)
  }

  return (
    <div className="h-full overflow-y-auto">
      {/* герой */}
      <section className="bg-lime px-4 pb-16 pt-8 md:px-8 md:pb-24 md:pt-12">
        <div className="mx-auto max-w-6xl">
          <h1 className="font-display text-[30px] font-bold leading-[1.08] tracking-tight text-ink md:text-[52px]">
            Скажите, куда хотите.
            <br />
            Остальное устроит агент!
          </h1>

          <div className="mt-6 flex flex-wrap gap-2">
            {MODES.map((m, i) => (
              <button
                key={m.label}
                onClick={() => setMode(i)}
                className={`flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition ${
                  i === mode ? 'bg-white text-brand shadow-card' : 'bg-white/45 text-ink hover:bg-white/70'
                }`}
              >
                <Icon name={m.icon} className="h-4 w-4" /> {m.label}
              </button>
            ))}
          </div>

          <form
            className="mt-3 flex flex-col gap-2 md:flex-row"
            onSubmit={(e) => {
              e.preventDefault()
              submit(text || MODES[mode].hint)
            }}
          >
            <label className="flex min-w-0 flex-1 flex-col rounded-2xl bg-white px-4 py-2.5 shadow-card focus-within:ring-2 focus-within:ring-brand">
              <span className="text-[11px] text-ink-muted">Опишите поездку своими словами</span>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={MODES[mode].hint}
                disabled={disabled}
                className="min-w-0 bg-transparent py-0.5 text-[16px] text-ink outline-none placeholder:text-ink-muted"
              />
            </label>
            <button
              type="submit"
              disabled={disabled}
              className="flex h-[60px] items-center justify-center gap-2 rounded-2xl bg-brand px-10 text-[16px] font-semibold text-white transition hover:bg-brand-hover disabled:opacity-60"
            >
              {connecting ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" /> Подключаюсь
                </>
              ) : (
                'Спланировать'
              )}
            </button>
          </form>
          <p className="mt-3 text-sm text-ink/70">
            Дорога туда и обратно, трансферы и отель — подберёт, сравнит, проверит и соберёт в один заказ.
          </p>
          {remembered.length > 0 && (
            <div className="mt-4 inline-flex flex-wrap items-center gap-x-2 gap-y-1 rounded-2xl bg-white/60 px-4 py-2.5 text-sm text-ink">
              <Icon name="clock" className="h-4 w-4 text-brand" />
              <span>
                <b>С возвращением!</b> Помню по прошлой поездке: {remembered.join(', ')} — учту как догадки, их можно убрать.
              </span>
              <button onClick={forget} className="font-semibold text-brand underline-offset-2 hover:underline">
                Забыть
              </button>
            </div>
          )}
        </div>
      </section>

      {/* белый лист поверх героя, как на ostrovok.ru */}
      <div className="relative -mt-8 rounded-t-[32px] bg-white px-4 pb-12 pt-8 md:-mt-10 md:rounded-t-[40px] md:px-8 md:pt-12">
        <div className="mx-auto max-w-6xl space-y-12">
          <section>
            <h2 className="font-display text-[22px] font-bold tracking-tight md:text-[30px]">С чего начать</h2>
            <p className="mt-1 text-ink-grey">Нажмите на сценарий — агент начнёт работу сразу</p>
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {EXAMPLES.map((e) => (
                <button
                  key={e.title}
                  onClick={() => submit(e.text)}
                  disabled={disabled}
                  className="group flex flex-col overflow-hidden rounded-2xl bg-canvas text-left transition hover:-translate-y-0.5 hover:shadow-lift disabled:opacity-60"
                >
                  <span className={`relative flex h-20 items-end bg-gradient-to-br p-3 sm:h-28 ${e.tint}`}>
                    <span className="absolute right-3 top-2 text-[36px] sm:text-[44px] leading-none drop-shadow">{e.emoji}</span>
                    <span className="rounded-full bg-white/90 px-2.5 py-0.5 text-[11px] font-semibold text-ink">{e.tag}</span>
                  </span>
                  <span className="flex flex-1 flex-col gap-1 p-3.5">
                    <span className="font-semibold leading-snug">{e.title}</span>
                    <span className="text-[13px] leading-snug text-ink-muted">«{e.text}»</span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section className="grid gap-3 lg:grid-cols-[1fr_1fr_380px]">
            <div className="grid gap-3 sm:grid-cols-2 lg:col-span-2">
              {FEATURES.map((f) => (
                <div key={f.title} className="rounded-2xl bg-canvas p-5">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-white text-brand shadow-card">
                    <Icon name={f.icon} className="h-5 w-5" />
                  </span>
                  <div className="mt-3 font-semibold">{f.title}</div>
                  <div className="mt-1 text-sm text-ink-grey">{f.text}</div>
                </div>
              ))}
            </div>
            <div className="guru-gradient relative flex flex-col justify-between overflow-hidden rounded-2xl p-6 text-white">
              <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-white/10" aria-hidden />
              <div className="relative">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-2.5 py-1 text-xs font-medium">
                  <Icon name="shield" className="h-3.5 w-3.5" /> без выдуманных цифр
                </span>
                <div className="mt-4 font-display text-[22px] font-bold leading-tight">
                  Каждую цену считает инструмент, каждую стыковку — проверяет валидатор
                </div>
                <p className="mt-3 text-sm text-white/80">
                  Нейросеть только объясняет. Перед оплатой план перепроверяется ещё раз, двойной клик не создаст второй заказ.
                </p>
              </div>
              <button
                onClick={() => submit(EXAMPLES[0].text)}
                disabled={disabled}
                className="relative mt-6 rounded-2xl bg-white py-3 text-sm font-semibold text-brand transition hover:bg-brand-light disabled:opacity-60"
              >
                Попробовать
              </button>
            </div>
          </section>

          <p className="text-xs text-ink-muted">
            Прототип для хакатона Островка. Города, аэропорты, климат и названия отелей — открытые данные (OpenStreetMap, Open-Meteo,
            Wikidata); цены, расписания и условия сгенерированы и помечены «демо-данные». Оплата имитируется.
          </p>
        </div>
      </div>
    </div>
  )
}
