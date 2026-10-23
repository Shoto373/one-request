import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { Checks, OfferRow } from '../components/BundleCard'
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

  if (isLoading) return <div className="p-6 text-sm text-slate-500">Загрузка…</div>
  if (error || !cart) return <div className="p-6 text-sm text-rose-700">Корзина не найдена</div>

  const b = cart.snapshot.bundle

  const pay = async (e: React.FormEvent) => {
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
      <div className="mx-auto grid max-w-5xl gap-6 p-6 md:grid-cols-[1fr_340px]">
        <section className="space-y-4">
          <Link to="/" className="text-sm text-brand hover:underline">
            ← к поездке
          </Link>
          <h1 className="text-2xl font-semibold">Оформление поездки</h1>
          <div className="rounded-xl border bg-white">
            <div className="border-b px-4 py-3 font-medium capitalize">Комплект «{b.label}»</div>
            <ul className="divide-y px-4">
              {b.items.map((o, i) => (
                <OfferRow key={o.offer_id + i} o={o} />
              ))}
            </ul>
            <div className="border-t px-4 py-3">
              <Checks checks={b.checks} />
            </div>
          </div>
          <p className="text-xs text-slate-500">
            Зафиксирована версия плана №{cart.snapshot.version}. Перед оплатой агент ещё раз проверит, что план не изменился.
          </p>
        </section>

        <aside className="h-fit space-y-4 rounded-xl border bg-white p-4 md:sticky md:top-6">
          <div className="flex items-baseline justify-between">
            <span className="text-slate-600">Итого</span>
            <span className="text-2xl font-semibold">{rub(cart.total_rub)}</span>
          </div>

          {cart.status === 'ordered' ? (
            <p className="text-sm text-emerald-700">Эта корзина уже оплачена.</p>
          ) : (
            <form onSubmit={pay} className="space-y-3">
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Имя и фамилия"
                className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <input
                required
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email для билетов"
                className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <div className="rounded-lg border border-dashed bg-slate-50 p-3 text-xs text-slate-500">
                Имитация оплаты: деньги не списываются, карта не нужна.
              </div>
              {payError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 p-2.5 text-sm text-rose-800">
                  {payError.message}
                  {(payError.code === 'plan_changed' || payError.code === 'plan_blocked') && (
                    <div className="mt-1">
                      <Link to="/" className="font-medium underline">
                        Вернуться к актуальному плану
                      </Link>
                    </div>
                  )}
                </div>
              )}
              <button
                type="submit"
                disabled={paying}
                className="w-full rounded-lg bg-emerald-600 py-2.5 font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
              >
                {paying ? 'Оплачиваю…' : `Оплатить ${rub(cart.total_rub)}`}
              </button>
            </form>
          )}
        </aside>
      </div>
    </div>
  )
}
