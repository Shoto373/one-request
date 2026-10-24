import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { OfferRow, SyntheticBadge } from '../components/BundleCard'
import Icon from '../components/Icon'
import { PageError, PageLoading } from '../components/PageState'
import { rub, when } from '../lib/format'

export default function OrderPage() {
  const { id = '' } = useParams()
  const { data: order, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['order', id],
    queryFn: () => api.getOrder(id),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  })

  if (isLoading) return <PageLoading text="Загружаю заказ…" />
  if (error || !order) {
    const missing = error instanceof ApiError && error.status === 404
    return (
      <PageError
        title={missing ? 'Заказ не найден' : 'Не удалось загрузить заказ'}
        text={missing ? 'Проверьте ссылку. Оплаченный заказ не пропадает — он хранится со всеми ценами.' : 'Сервер не ответил. Заказ сохранён — повторите загрузку.'}
        onRetry={missing ? undefined : () => refetch()}
        retrying={isFetching}
      />
    )
  }

  const b = order.snapshot.bundle
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-8">
        <div className="guru-gradient relative overflow-hidden rounded-3xl p-6 text-white">
          <div className="absolute -right-8 -top-10 h-40 w-40 rounded-full bg-lime/80 blur-2xl" aria-hidden />
          <div className="relative">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/20 px-2.5 py-1 text-xs font-semibold">
              <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.6} /> Оплачено · имитация
            </span>
            <h1 className="mt-3 text-[30px] font-bold leading-tight tracking-tight">Поездка оформлена!</h1>
            <p className="mt-1 text-sm text-white/85">
              Заказ <span className="font-mono">{order.order_id.slice(0, 8)}</span> · платёж {order.payment.ref} · {when(order.payment.paid_at)}
            </p>
          </div>
        </div>

        <section className="card">
          <header className="flex items-center justify-between border-b border-black/[.05] p-4">
            <span className="flex items-center gap-1.5">
              <span className="rounded-full bg-brand-light px-2.5 py-1 text-xs font-bold uppercase tracking-wide text-brand">{b.label}</span>
              {b.items.some((o) => o.synthetic) && <SyntheticBadge />}
            </span>
            <span className="price text-2xl">{rub(order.total_rub)}</span>
          </header>
          <ol className="px-4 pt-4">
            {b.items.map((o, i) => (
              <OfferRow key={o.offer_id + i} o={o} last={i === b.items.length - 1} markSynthetic={false} />
            ))}
          </ol>
        </section>

        <p className="text-xs text-ink-muted">
          Состав и цены зафиксированы в момент оплаты и больше не меняются. Плательщик: {order.payer.name}, {order.payer.email}.
        </p>
        <Link to="/" className="btn-soft">
          <Icon name="back" className="h-4 w-4" /> к поездке
        </Link>
      </div>
    </div>
  )
}
