import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { Checks, OfferRow, SyntheticBadge } from '../components/BundleCard'
import Icon from '../components/Icon'
import { rub } from '../lib/format'

/** Имитация оплаты. Один idempotency_key на страницу: двойной клик / повтор запроса не создают второй заказ. */
export default function CheckoutPage() {
  const { cartId = '' } = useParams()
  const navigate = useNavigate()
  const { data: cart, isLoading, error } = useQuery({ queryKey: ['cart', cartId], queryFn: () => api.getCart(cartId) })
  const keyRef = useRef(`pay-${crypto.randomUUID()}`)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [paying, setPaying] = useState(false)
  const [payError, setPayError] = useState<{ code: string; message: string } | null>(null)

  if (isLoading) return <div className="p-6 text-sm text-ink-muted">Загрузка…</div>
  if (error || !cart) return <div className="p-6 text-sm text-bad">Корзина не найдена</div>

  const b = cart.snapshot.bundle

  const pay = async (e: FormEvent) => {
    e.preventDefault()
    if (paying) return
    setPaying(true)
    setPayError(null)
    try {
      const order = await api.checkout(cartId, { payer: { name, email }, idempotency_key: keyRef.current })
      navigate(`/orders/${order.order_id}`, { replace: true })
    } catch (err) {
      setPayError(err instanceof ApiError ? err.detail : { code: 'network', message: 'Сеть недоступна, попробуйте ещё раз' })
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
                <OfferRow key={o.offer_id + i} o={o} last={i === b.items.length - 1} markSynthetic={false} />
              ))}
            </ol>
            <div className="border-t border-black/[.05] bg-canvas/50 p-4">
              <Checks checks={b.checks} />
            </div>
          </div>
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
                {payError && (
                  <div className="rounded-xl bg-bad-bg p-3 text-sm text-bad">
                    {payError.message}
                    {(payError.code === 'plan_changed' || payError.code === 'plan_blocked') && (
                      <Link to="/" className="mt-1 block font-semibold underline">Вернуться к актуальному плану</Link>
                    )}
                  </div>
                )}
                <button type="submit" disabled={paying} className="btn-primary w-full py-3.5 text-base">
                  {paying ? 'Оплачиваю…' : `Оплатить ${rub(cart.total_rub)}`}
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
