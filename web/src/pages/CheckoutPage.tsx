import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { Checks, OfferRow, SyntheticBadge, attachChecks } from '../components/BundleCard'
import Icon from '../components/Icon'
import ReplanDiff from '../components/ReplanDiff'
import { PageError, PageLoading } from '../components/PageState'
import { diffPlans } from '../lib/diff'
import { rub } from '../lib/format'
import { profileId } from '../lib/profile'
import { uuid } from '../lib/uuid'

/** Имитация оплаты. Один idempotency_key на страницу: двойной клик / повтор запроса не создают второй заказ. */
export default function CheckoutPage() {
  const { cartId = '' } = useParams()
  const navigate = useNavigate()
  const { data: cart, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['cart', cartId],
    queryFn: () => api.getCart(cartId),
    // 404 — повторять бессмысленно; сеть — пробуем ещё пару раз
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  })
  const keyRef = useRef(`pay-${uuid()}`)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [paying, setPaying] = useState(false)
  const [payError, setPayError] = useState<{ code: string; message: string } | null>(null)
  // C4: следим, не ушёл ли план вперёд, пока пользователь на странице оплаты
  const sessionId = cart?.snapshot.session_id
  const { data: live, refetch: refetchLive } = useQuery({
    queryKey: ['session-version', sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: !!sessionId && cart?.status === 'open',
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  })

  if (isLoading) return <PageLoading text="Загружаю корзину…" />
  if (error || !cart) {
    const missing = error instanceof ApiError && error.status === 404
    return (
      <PageError
        title={missing ? 'Корзина не найдена' : 'Не удалось загрузить корзину'}
        text={missing ? 'Возможно, ссылка устарела. Соберите поездку заново — агент сохранит ваш план.' : 'Сервер не ответил. Проверьте подключение и повторите.'}
        onRetry={missing ? undefined : () => refetch()}
        retrying={isFetching}
      />
    )
  }

  const b = cart.snapshot.bundle
  const { byOffer, general } = attachChecks(b)
  const liveVersion = live?.state.version
  const stale = cart.status === 'open' && liveVersion != null && liveVersion !== cart.snapshot.version
  // что поменялось в плане относительно корзины: та же логика «сохранено / заменено», что и на холсте
  const liveBundle = live && (live.state.bundles.find((x) => x.id === live.state.selected_bundle_id) ?? live.state.bundles[0])
  const staleDiff = stale && live
    ? diffPlans({ ...live.state, version: cart.snapshot.version, bundles: [b], selected_bundle_id: b.id }, live.state)
    : null

  const pay = async (e: FormEvent) => {
    e.preventDefault()
    if (paying) return
    setPaying(true)
    setPayError(null)
    try {
      const order = await api.checkout(cartId, { payer: { name, email }, idempotency_key: keyRef.current, profile_id: profileId() })
      navigate(`/orders/${order.order_id}`, { replace: true })
    } catch (err) {
      setPayError(err instanceof ApiError ? err.detail : { code: 'network', message: 'Сеть недоступна. Повторите — второй раз деньги не спишутся.' })
      // план ушёл вперёд — сразу подтягиваем свежую версию плана и корзины, чтобы показать, что изменилось
      if (err instanceof ApiError && err.status === 409) {
        refetchLive()
        refetch()
      }
    } finally {
      setPaying(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-6xl gap-6 p-4 md:grid-cols-[1fr_380px] md:p-8">
        <section className="space-y-4">
          <Link to="/" className="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
            <Icon name="back" className="h-4 w-4" /> к поездке
          </Link>
          <h1 className="text-[28px] font-bold tracking-tight">Оформление поездки</h1>
          <div className="card">
            <div className="flex items-center justify-between border-b border-black/[.05] p-4">
              <span className="flex items-center gap-1.5">
              <span className="rounded-full bg-brand-light px-2.5 py-1 text-xs font-bold uppercase tracking-wide text-brand">{b.label}</span>
              {b.items.some((o) => o.synthetic) && <SyntheticBadge />}
            </span>
              <span className="text-xs text-ink-muted">версия плана №{cart.snapshot.version}</span>
            </div>
            <ol className="px-4 pt-4">
              {b.items.map((o, i) => (
                <OfferRow key={o.offer_id + i} o={o} last={i === b.items.length - 1} markSynthetic={false} checks={byOffer.get(o.offer_id)} />
              ))}
            </ol>
            {general.length > 0 && (
              <div className="border-t border-black/[.05] bg-canvas/50 p-4">
                <Checks checks={general} />
              </div>
            )}
          </div>
          {stale && staleDiff && <ReplanDiff diff={staleDiff} />}
          {stale && live && !liveBundle && (
            <div className="card p-4 text-sm text-ink-grey">
              В актуальной версии плана комплектов пока нет — агент ещё уточняет условия. Вернитесь к поездке, чтобы продолжить.
            </div>
          )}
        </section>

        <aside className="h-fit space-y-4 md:sticky md:top-8 md:mt-[76px]">
          <div className="card p-5">
            <div className="flex items-baseline justify-between">
              <span className="text-ink-grey">Итого</span>
              <span className="price text-[30px]">{rub(cart.total_rub)}</span>
            </div>
            <div className="mt-1 text-xs text-ink-muted">за всех путешественников и весь период</div>

            {cart.status === 'ordered' ? (
              <p className="mt-4 rounded-xl bg-ok-bg p-3 text-sm font-medium text-ok">Эта корзина уже оплачена.</p>
            ) : (
              <form onSubmit={pay} className="mt-5 space-y-3">
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-ink-grey">Имя и фамилия</span>
                  <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Иван Петров"
                    className="w-full rounded-2xl border border-black/10 px-4 py-3 text-[15px] outline-none transition focus:border-brand focus:shadow-lift" />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-ink-grey">Email для билетов</span>
                  <input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ivan@example.com"
                    className="w-full rounded-2xl border border-black/10 px-4 py-3 text-[15px] outline-none transition focus:border-brand focus:shadow-lift" />
                </label>
                {stale && (
                  <div className="flex gap-2.5 rounded-xl bg-warn-bg p-3 text-sm text-[#8a4b00]">
                    <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <b>План обновился</b> (версия {cart.snapshot.version} → {liveVersion}). Оплатить устаревший нельзя —
                      проверьте новую версию и оформите её.
                      {liveBundle && liveBundle.total_rub !== cart.total_rub && (
                        <> Новый итог — <b className="tabular-nums">{rub(liveBundle.total_rub)}</b>.</>
                      )}
                      <Link to="/" className="mt-1 block font-semibold underline">Открыть актуальный план</Link>
                    </div>
                  </div>
                )}
                {payError && (
                  <div className="rounded-xl bg-bad-bg p-3 text-sm text-bad">
                    {payError.message}
                    {(payError.code === 'plan_changed' || payError.code === 'plan_blocked') && (
                      <Link to="/" className="mt-1 block font-semibold underline">Вернуться к актуальному плану</Link>
                    )}
                  </div>
                )}
                <button type="submit" disabled={paying || stale} className="btn-primary w-full py-3.5 text-base">
                  {paying ? 'Оплачиваю…' : stale ? 'План изменился' : payError?.code === 'network' ? 'Повторить оплату' : `Оплатить ${rub(cart.total_rub)}`}
                </button>
              </form>
            )}
          </div>
          <div className="flex gap-3 rounded-2xl bg-brand-light p-4 text-[13px] text-ink-grey">
            <Icon name="shield" className="h-5 w-5 shrink-0 text-brand" />
            <span>
              <b className="text-ink">Имитация оплаты.</b> Деньги не списываются. Перед оплатой агент ещё раз проверит, что план не изменился.
            </span>
          </div>
        </aside>
      </div>
    </div>
  )
}
