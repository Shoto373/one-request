import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { profileId } from '../lib/profile'
import Icon, { type IconName } from './Icon'

const SCENARIOS = [
  { title: 'Выдохнуть у моря', subtitle: 'Анталья, Шарм, Пхукет — где теплее', image: '/sochi-coast.png', query: 'Хочу к тёплому морю на неделю, двое взрослых' },
  { title: 'Путешествие с ребёнком', subtitle: 'Удобная дорога и время вместе', image: '/sochi-family.png', query: 'В Казань на выходные, двое взрослых и ребёнок 5 лет' },
  { title: 'Взять друга с собой', subtitle: 'Проверим условия для питомца', image: '/sochi-dog.png', query: 'В Калининград на неделю, двое взрослых и собака' },
]

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: 'refresh', title: 'Пересоберёт поездку', text: 'Измените даты или состав — агент заново проверит предложения и объяснит, что поменялось.' },
  { icon: 'shield', title: 'Покажет ограничения', text: 'Неизвестные условия и неподтверждённые стыковки будут отмечены до имитации оформления.' },
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
  const [text, setText] = useState('')
  const forget = async () => {
    if (!pid) return
    await api.forgetProfile(pid).catch(() => undefined)
    qc.invalidateQueries({ queryKey: ['profile', pid] })
  }
  const submit = (value: string) => {
    const query = value.trim()
    if (!query || disabled) return
    onSend(query)
  }
  const submitHero = () => submit(text.trim() || 'Хочу куда-нибудь на неделю')

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-white">
      <section className="shrink-0 bg-lime px-4 pb-10 pt-5 md:px-8 md:pb-14 md:pt-8">
        <div className="mx-auto max-w-[1120px]">
          <div className="text-[11px] font-bold tracking-[.18em] text-ink">ВАШ ГОРОД <span className="px-1">→</span> ЛЮБАЯ ТОЧКА МИРА</div>
          <h1 className="mt-5 max-w-[880px] font-display text-[34px] font-bold leading-[1.1] tracking-tight text-ink sm:text-[44px] md:mt-6 md:text-[56px]">
            Куда угодно — из вашего города
          </h1>
          <form className="mt-5 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); submitHero() }}>
            <label className="flex min-h-[64px] min-w-0 flex-1 flex-col justify-center rounded-2xl bg-white px-4 py-2 shadow-card">
              <span className="text-[11px] text-ink-muted">Расскажите о поездке</span>
              <input value={text} onChange={(e) => setText(e.target.value)} disabled={disabled}
                placeholder="Из Екатеринбурга в Стамбул на неделю, двое взрослых, до 150 тысяч рублей"
                className="w-full bg-transparent text-base text-ink outline-none placeholder:text-ink-muted/90 md:text-[17px]" />
            </label>
            <button type="submit" disabled={disabled} className="btn-primary h-14 shrink-0 px-8 text-base sm:h-16">
              {connecting ? 'Подключаюсь…' : 'Собрать поездку →'}
            </button>
          </form>
          <p className="mt-3 max-w-[850px] text-sm leading-relaxed text-ink-grey md:text-[15px]">Расскажите, откуда и когда хотите поехать. Агент найдёт варианты дороги и проживания.</p>
        </div>
      </section>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-8 pt-8 md:px-8 md:pt-10">
        <div className="text-[11px] font-bold tracking-[.18em] text-brand">МОРЕ <span className="px-1 text-brand/50">·</span> ГОРОДА <span className="px-1 text-brand/50">·</span> ГОРЫ</div>
        <h2 className="mt-2 font-display text-[31px] font-bold leading-[1.1] tracking-tight text-ink md:text-[48px]">Поездка начинается с вашей идеи.</h2>
        <p className="mt-2 text-base text-ink-grey md:text-[19px]">Выберите настроение поездки — детали обсудим с агентом.</p>

        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {SCENARIOS.map((scenario) => (
            <button key={scenario.title} onClick={() => submit(scenario.query)} disabled={disabled}
              className="group relative isolate flex h-[238px] overflow-hidden rounded-[20px] text-left text-white shadow-card transition hover:-translate-y-1 hover:shadow-lift disabled:opacity-60 sm:h-[260px] md:h-[292px]">
              <img src={scenario.image} alt="" className="absolute inset-0 -z-10 h-full w-full object-cover transition duration-500 group-hover:scale-[1.03]" />
              <span className="absolute inset-0 -z-[1] bg-gradient-to-t from-[#10223f]/85 via-[#10223f]/10 to-transparent" />
              <span className="absolute inset-x-4 bottom-4 pr-12">
                <span className="block font-display text-[20px] font-semibold leading-tight md:text-[23px]">{scenario.title}</span>
                <span className="mt-1 block text-sm text-white/90">{scenario.subtitle}</span>
              </span>
              <span className="absolute right-4 top-4 grid h-10 w-10 place-items-center rounded-full bg-white text-ink shadow-card transition group-hover:translate-x-0.5">
                <Icon name="chevron" className="h-5 w-5" />
              </span>
            </button>
          ))}
        </div>

        <section className="mt-8 grid gap-5 md:grid-cols-[.78fr_1.22fr] md:items-center">
          <div className="py-2">
            <h3 className="max-w-md font-display text-[29px] font-bold leading-[1.12] tracking-tight md:text-[38px]">Планы меняются.<br />Поездка подстроится.</h3>
            <p className="mt-3 max-w-md text-base leading-relaxed text-ink-grey">Сдвиньте даты или измените пожелания — агент пересоберёт маршрут и объяснит, что сохранилось, что заменилось и почему.</p>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 md:grid-cols-1">
              {FEATURES.map((feature) => (
                <div key={feature.title} className="flex gap-3 rounded-2xl bg-canvas p-3.5">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white text-brand shadow-card"><Icon name={feature.icon} className="h-5 w-5" /></span>
                  <span><span className="block text-sm font-semibold">{feature.title}</span><span className="mt-0.5 block text-xs leading-relaxed text-ink-grey">{feature.text}</span></span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[22px] border border-brand/20 bg-white p-4 shadow-card md:p-5">
            <div className="mb-3 flex items-center justify-between gap-2">
              <span className="text-[10px] font-bold tracking-[.16em] text-brand">ПРИМЕР ПЕРЕСБОРКИ</span>
              <span className="rounded-full bg-brand-light px-2.5 py-1 text-[11px] font-medium text-brand">без бронирования</span>
            </div>
            <div className="mb-4 inline-flex max-w-full rounded-2xl rounded-bl-md bg-brand-light px-3.5 py-2 text-sm text-ink">«Давайте на два дня позже»</div>
            <div className="space-y-0">
              <ReplanRow icon="plane" title="Дорога" badge="Новые даты" tone="blue">12–19 ноября <span className="px-1 text-ink-muted">→</span> 14–21 ноября</ReplanRow>
              <ReplanRow icon="bed" title="Отель" badge="Нужно проверить" tone="green">Заново проверим наличие и цену на новые даты.</ReplanRow>
              <ReplanRow icon="car" title="Трансфер" badge="Не включён" tone="amber" last>Покажем отдельно, если поиск и подтверждённая цена доступны.</ReplanRow>
            </div>
          </div>
        </section>

        {remembered.length > 0 && <div className="mt-5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-2xl bg-brand-light/70 px-4 py-3 text-sm text-ink">
          <Icon name="clock" className="h-4 w-4 text-brand" /><span><b>С возвращением!</b> Помню по прошлой поездке: {remembered.join(', ')}. Это только догадки, их можно убрать.</span>
          <button onClick={forget} className="font-semibold text-brand underline-offset-2 hover:underline">Забыть</button>
        </div>}

        <p className="mt-5 text-[11px] text-ink-muted">Билеты, поезда и отели ищутся в реальном времени через Туту и Островок. Оформление имитируется.</p>

        <section className="mt-7 rounded-2xl bg-[#eaf2ff] p-3 md:flex md:items-center md:gap-4 md:p-4">
          <h3 className="font-display text-lg font-bold md:shrink-0 md:text-[21px]">А какая поездка нужна вам?</h3>
          <form className="mt-2 flex flex-1 flex-col gap-2 sm:flex-row md:mt-0" onSubmit={(e) => { e.preventDefault(); submit(text || 'Хочу куда-нибудь на неделю') }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Расскажите о своих планах…" disabled={disabled}
              className="h-11 min-w-0 flex-1 rounded-xl border border-black/[.06] bg-white px-4 text-sm outline-none placeholder:text-ink-muted focus:border-brand/40 focus:ring-2 focus:ring-brand/10" />
            <button type="submit" disabled={disabled} className="btn-primary h-11 shrink-0 px-5 md:px-7">{connecting ? 'Подключаюсь…' : 'Открыть агента'} <Icon name="send" className="h-4 w-4" /></button>
          </form>
        </section>
      </main>
    </div>
  )
}

function ReplanRow({ icon, title, badge, tone, last, children }: {
  icon: IconName; title: string; badge: string; tone: 'blue' | 'green' | 'amber'; last?: boolean; children: React.ReactNode
}) {
  const badgeStyle = tone === 'blue' ? 'bg-brand-light text-brand' : tone === 'green' ? 'bg-ok-bg text-[#347d17]' : 'bg-warn-bg text-[#9a5900]'
  return <div className="grid grid-cols-[34px_1fr] gap-x-3">
    <div className="relative flex justify-center">{!last && <span className="absolute bottom-0 top-8 w-px bg-brand/20" />}<span className="relative z-[1] grid h-8 w-8 place-items-center rounded-full bg-canvas text-brand"><Icon name={icon} className="h-4 w-4" /></span></div>
    <div className={`min-w-0 pb-3 pt-0.5 ${last ? '' : 'border-b border-black/[.05]'}`}>
      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{title}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${badgeStyle}`}>{badge}</span></div>
      <div className="mt-1 text-xs leading-relaxed text-ink-grey">{children}</div>
    </div>
  </div>
}

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
