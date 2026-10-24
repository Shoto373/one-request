import { Link } from 'react-router-dom'
import Icon from './Icon'

/** Загрузка страницы корзины/заказа — каркас вместо голого «Загрузка…» */
export function PageLoading({ text }: { text: string }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 md:p-8" role="status">
      <div className="flex items-center gap-2.5 text-sm text-ink-grey">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />
        {text}
      </div>
      <div className="card animate-pulse space-y-4 p-5">
        <div className="h-6 w-48 rounded bg-black/[.07]" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex gap-3">
            <div className="h-9 w-9 rounded-xl bg-black/[.06]" />
            <div className="flex-1 space-y-1.5 pt-1">
              <div className="h-3.5 w-2/3 rounded bg-black/[.06]" />
              <div className="h-3 w-1/3 rounded bg-black/[.05]" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** Ошибка загрузки: чем помочь пользователю — повторить или вернуться к поездке */
export function PageError({ title, text, onRetry, retrying }: { title: string; text: string; onRetry?: () => void; retrying?: boolean }) {
  return (
    <div className="mx-auto max-w-md p-4 pt-12 text-center md:p-8 md:pt-20">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-bad-bg text-bad">
        <Icon name="alert" className="h-6 w-6" />
      </span>
      <h1 className="mt-4 text-xl font-bold">{title}</h1>
      <p className="mt-1.5 text-sm text-ink-grey">{text}</p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {onRetry && (
          <button onClick={onRetry} disabled={retrying} className="btn-primary">
            <Icon name="refresh" className={`h-4 w-4 ${retrying ? 'animate-spin' : ''}`} /> {retrying ? 'Загружаю…' : 'Повторить'}
          </button>
        )}
        <Link to="/" className="btn-soft">
          <Icon name="back" className="h-4 w-4" /> к поездке
        </Link>
      </div>
    </div>
  )
}
