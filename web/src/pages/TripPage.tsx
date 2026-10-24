import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import Chat from '../components/Chat'
import Icon from '../components/Icon'
import Landing from '../components/Landing'
import TracePanel from '../components/TracePanel'
import TripCanvas from '../components/TripCanvas'
import { reqLabel } from '../lib/format'
import { useTripSession } from '../lib/useTripSession'

export default function TripPage() {
  const navigate = useNavigate()
  const s = useTripSession()
  const [checkoutBusy, setCheckoutBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<'chat' | 'trip'>('chat')
  const [traceOpen, setTraceOpen] = useState(false)

  const toCart = async (bundleId: string) => {
    if (!s.state || !s.sessionId) return
    setCheckoutBusy(bundleId)
    try {
      // Persist the user's explicit choice through the agent before asking it
      // to validate readiness. The cart API still checks this version again.
      // act сам показывает ошибку (и перечитывает план на 409) — здесь просто останавливаемся
      if (!(await s.act('select_bundle', { bundle_id: bundleId }, `Выбираю комплект ${bundleId}`))) return
      let current = (await api.getSession(s.sessionId)).state
      if (current.selected_bundle_id !== bundleId || current.stage !== 'assemble') {
        throw new Error('Не удалось зафиксировать выбранный комплект. Обновите план и повторите действие.')
      }
      if (!(await s.act('request_checkout', {}, 'Проверь выбранный комплект перед оформлением'))) return
      current = (await api.getSession(s.sessionId)).state
      if (current.stage !== 'checkout') {
        const blockers = current.bundles.find((b) => b.id === bundleId)?.checks.filter((c) => c.status === 'block').map((c) => c.text) ?? []
        throw new Error(blockers.length ? `Нельзя оформить: ${blockers.join('; ')}` : 'Проверка плана не подтвердила готовность к оформлению.')
      }
      const cart = await api.createCart({ session_id: s.sessionId, version: current.version, bundle_id: bundleId })
      navigate(`/checkout/${cart.cart_id}`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // план ушёл вперёд между проверкой и корзиной — показываем актуальный, корзину не создаём
        await s.refresh()
        s.setError('Пока шла проверка, план изменился. Посмотрите актуальную версию и оформите ещё раз.')
      } else {
        s.setError(e instanceof ApiError ? e.message : e instanceof TypeError ? 'Нет связи с сервером — попробуйте ещё раз.' : e instanceof Error ? e.message : 'Не удалось оформить')
      }
    } finally {
      setCheckoutBusy(null)
    }
  }

  const found = (s.state?.bundles.length ?? 0) + (s.state?.destinations.length ?? 0)
  // пока диалога нет — стартовый экран в стиле ostrovok.ru, после первого сообщения — рабочее пространство
  const landing = s.messages.length === 0 && !s.busy && !s.state?.requirements.length
  // мобильный: вариантов стало больше, чем пользователь видел на вкладке «Поездка» — подсказываем перейти
  const [seen, setSeen] = useState(0)
  useEffect(() => {
    if (tab === 'trip') setSeen(found)
  }, [tab, found])
  const openTrip = () => setTab('trip')

  return (
    <div className="flex h-full flex-col">
      {s.notice && (
        <div
          role={s.notice.tone === 'error' ? 'alert' : 'status'}
          className={`flex items-center gap-3 border-b px-4 py-2.5 text-sm ${
            s.notice.tone === 'error' ? 'border-bad/20 bg-bad-bg text-bad' : 'border-brand/15 bg-brand-light text-brand'
          }`}
        >
          <Icon name={s.notice.tone === 'error' ? 'alert' : 'refresh'} className="h-4 w-4 shrink-0" />
          <span className="flex-1">{s.notice.text}</span>
          {s.notice.retry && (
            <button onClick={s.retry} disabled={s.busy} className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-semibold shadow-card hover:shadow-lift">
              Повторить
            </button>
          )}
          <button onClick={() => s.setError(null)} className="rounded-full p-1 hover:bg-black/5" aria-label="Закрыть">
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>
      )}

      {landing ? (
        <div className="min-h-0 flex-1">
          <Landing disabled={!s.sessionId} connecting={s.connecting} onSend={s.send} />
        </div>
      ) : (
      <>
      {/* мобильные вкладки */}
      <div className="flex gap-1 border-b border-black/[.06] bg-white p-1.5 text-sm font-semibold md:hidden">
        {(['chat', 'trip'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-xl py-2 ${tab === t ? 'bg-brand-light text-brand' : 'text-ink-grey'}`}
          >
            {t === 'chat' ? 'Диалог' : `Поездка${found ? ` · ${found}` : ''}`}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1">
        <section className={`${tab === 'chat' ? 'flex' : 'hidden'} w-full flex-col border-r border-black/[.06] bg-white md:flex md:w-[440px] md:shrink-0`}>
          <div className="flex items-center justify-between border-b border-black/[.06] px-4 py-2.5">
            <span className="text-sm font-semibold">Диалог с агентом</span>
            <div className="flex gap-1">
              <button onClick={() => setTraceOpen((o) => !o)} className={`btn-ghost px-2.5 py-1.5 text-xs ${traceOpen ? 'bg-brand-light text-brand' : ''}`}>
                <Icon name="list" className="h-4 w-4" /> Шаги агента
                {s.trace.length > 0 && <span className="rounded-full bg-brand px-1.5 text-[10px] text-white">{s.trace.length}</span>}
              </button>
              <button onClick={s.restart} disabled={s.busy} className="btn-ghost px-2.5 py-1.5 text-xs">
                <Icon name="refresh" className="h-4 w-4" /> Заново
              </button>
            </div>
          </div>
          <div className="relative min-h-0 flex-1">
            {tab === 'chat' && !s.busy && found > seen && (
              <button
                onClick={openTrip}
                className="absolute inset-x-4 bottom-[76px] z-10 flex items-center justify-center gap-2 rounded-2xl bg-ink py-3 text-sm font-semibold text-white shadow-lift md:hidden"
              >
                {s.state?.bundles.length ? 'Смотреть комплекты' : 'Смотреть направления'} · {found}
                <Icon name="chevron" className="h-4 w-4" />
              </button>
            )}
            <Chat
              messages={s.messages}
              steps={s.steps}
              questions={s.state?.open_questions ?? []}
              busy={s.busy}
              disabled={!s.sessionId}
              connecting={s.connecting}
              onSend={s.send}
              onRetry={s.send}
              onAnswer={(_q, v) => s.send(v)}
            />
          </div>
        </section>

        <section className={`${tab === 'trip' ? 'block' : 'hidden'} min-w-0 flex-1 md:block`}>
          <TripCanvas
            state={s.state}
            busy={s.busy}
            checkoutBusyId={checkoutBusy}
            // правка — свободным текстом: агент сам разберёт значение (бюджет, даты, состав)
            onEditRequirement={(r, text) => s.send(`Исправь: ${reqLabel(r)} → ${text}`)}
            onRemoveRequirement={(r) =>
              r.kind === 'assumption'
                ? s.act('remove_assumption', { key: r.key }, `Не учитывай: ${reqLabel(r)}`)
                : s.act('remove_requirement', { key: r.key }, `Убери условие: ${reqLabel(r)}`)
            }
            // подтвердить догадку = сделать её обязательной с тем же значением
            onConfirmRequirement={(r) =>
              s.act('edit_requirement', { key: r.key, value: r.value, kind: 'hard', label: r.label ?? reqLabel(r) }, `Да, это верно: ${reqLabel(r)}`)
            }
            onSelectDestination={(d) => s.act('select_destination', { destination_id: d.id }, `Выбираю ${d.name}`)}
            onApplyChange={(c) => s.act('accept_alternative', { alternative_id: c.id }, `Согласен: ${c.label}`)}
            onSelectBundle={(b) => s.act('select_bundle', { bundle_id: b.id }, `Выбираю комплект «${b.label}»`)}
            diff={s.diff}
            onCloseDiff={s.clearDiff}
            onCheckout={toCart}
          />
        </section>

        {traceOpen && (
          <div className="fixed inset-0 z-30 bg-black/30 md:static md:z-auto md:w-[380px] md:shrink-0 md:border-l md:border-black/[.06] md:bg-transparent" onClick={() => setTraceOpen(false)}>
            <div className="ml-auto h-full w-[min(380px,92vw)] md:w-full" onClick={(e) => e.stopPropagation()}>
              <TracePanel trace={s.trace} onClose={() => setTraceOpen(false)} />
            </div>
          </div>
        )}
      </div>
      </>
      )}
    </div>
  )
}
