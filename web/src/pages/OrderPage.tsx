import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { OfferRow } from '../components/BundleCard'
import { rub, when } from '../lib/format'

export default function OrderPage() {
  const { id = '' } = useParams()
  const { data: order, isLoading, error } = useQuery({ queryKey: ['order', id], queryFn: () => api.getOrder(id) })

  if (isLoading) return <div className="p-6 text-sm text-slate-500">Загрузка…</div>
  if (error || !order) return <div className="p-6 text-sm text-rose-700">Заказ не найден</div>

  const b = order.snapshot.bundle
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-6">
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
          <div className="text-sm text-emerald-700">Оплачено (имитация)</div>
          <h1 className="text-2xl font-semibold">Поездка оформлена</h1>
          <div className="mt-1 text-sm text-slate-600">
            Заказ <span className="font-mono">{order.order_id.slice(0, 8)}</span> · платёж {order.payment.ref} ·{' '}
            {when(order.payment.paid_at)}
          </div>
        </div>

        <section className="rounded-xl border bg-white">
          <header className="flex items-baseline justify-between border-b px-4 py-3">
            <span className="font-medium capitalize">Комплект «{b.label}»</span>
            <span className="text-lg font-semibold">{rub(order.total_rub)}</span>
          </header>
          <ul className="divide-y px-4">
            {b.items.map((o, i) => (
              <OfferRow key={o.offer_id + i} o={o} />
            ))}
          </ul>
        </section>

        <p className="text-xs text-slate-500">
          Состав и цены зафиксированы в момент оплаты и больше не меняются. Плательщик: {order.payer.name}, {order.payer.email}.
        </p>
        <Link to="/" className="inline-block text-sm text-brand hover:underline">
          ← к поездке
        </Link>
      </div>
    </div>
  )
}
