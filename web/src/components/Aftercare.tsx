import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import type { DateShiftQuote, Reminder } from '../api/types'
import Icon, { type IconName } from './Icon'
import { localParts, rub } from '../lib/format'
import { uuid } from '../lib/uuid'

/** После покупки: что дальше по времени, что будет при отмене, сдвиг дат с расчётом денег, ранний заезд. */

const REMINDER_ICON: Record<Reminder['kind'], IconName> = {
  checkin_online: 'passport',
  departure: 'plane',
  transfer: 'car',
  checkin: 'bed',
  checkout: 'bed',
  free_cancel_deadline: 'clock',
}

function when(iso: string) {
  const p = localParts(iso)
  return `${p.date}, ${p.time}`
}

/** 2026-11-12 → «12 нояб.» */
function day(iso?: string) {
  return iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) : ''
}

export default function Aftercare({ orderId }: { orderId: string }) {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['aftercare', orderId], queryFn: () => api.getAftercare(orderId) })

  if (isLoading) return <div className="card animate-pulse p-5 text-sm text-ink-muted">Загружаю, что можно сделать с поездкой…</div>
  if (error || !data)
    return (
      <div className="card flex items-center justify-between p-4 text-sm text-bad">
        Не удалось загрузить действия после покупки.
        <button onClick={() => refetch()} className="btn-soft py-1.5">Повторить</button>
      </div>
    )

  const changed = data.changes.filter((c) => c.kind === 'date_shift')
  const reload = () => qc.invalidateQueries({ queryKey: ['aftercare', orderId] })

  return (
    <div className="space-y-4">
      <h2 className="font-display text-[22px] font-bold tracking-tight">После покупки</h2>

      {data.changes.length > 0 && (
        <section className="card divide-y divide-black/[.05]">
          {data.changes.map((c) => (
            <div key={c.change_id} className="flex items-start gap-3 p-4 text-sm">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ok-bg text-ok">
                <Icon name="check" className="h-4 w-4" strokeWidth={2.4} />
              </span>
              <div className="flex-1">
                {c.kind === 'date_shift' ? (
                  <>
                    <b>Даты изменены</b> на {day(c.quote.new_dates?.start)} — {day(c.quote.new_dates?.end)}.{' '}
                    {c.quote.to_pay_rub > 0 ? `Доплачено ${rub(c.quote.to_pay_rub)}` : c.quote.to_pay_rub < 0 ? `К возврату ${rub(-c.quote.to_pay_rub)}` : 'Без доплаты'}
                    {c.quote.penalty_total_rub > 0 && <span className="text-ink-muted"> (из них штрафы за отмену {rub(c.quote.penalty_total_rub)})</span>}
                  </>
                ) : (
                  <>
                    <b>Ранний заезд</b> с {c.quote.from}: {c.quote.status_text}
                  </>
                )}
              </div>
              <span className="text-xs text-ink-muted">{when(c.created_at)}</span>
            </div>
          ))}
          {changed.length > 0 && (
            <div className="flex items-center justify-between bg-canvas/60 p-4 text-sm">
              <span className="text-ink-grey">Действующий состав поездки</span>
              <span className="price text-lg">{rub(data.total_rub)}</span>
            </div>
          )}
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Timeline reminders={data.reminders} />
        <div className="space-y-4">
          <DateShift orderId={orderId} onDone={reload} />
          {data.early_checkin && <EarlyCheckin orderId={orderId} option={data.early_checkin} done={data.changes.some((c) => c.kind === 'early_checkin')} onDone={reload} />}
        </div>
      </div>

      <section className="card p-4">
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="font-semibold">Если отменить прямо сейчас</h3>
          <span className={`price ${data.cancellation.penalty_total_rub ? 'text-bad' : 'text-ok'}`}>
            {data.cancellation.penalty_total_rub ? `потеряете ${rub(data.cancellation.penalty_total_rub)}` : 'бесплатно'}
          </span>
        </div>
        <ul className="space-y-1.5 text-sm">
          {data.cancellation.items.map((i, n) => (
            <li key={i.offer_id + n} className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-ink-grey">{i.title}</span>
              <span className={`shrink-0 text-xs font-semibold ${i.status === 'free' ? 'text-ok' : 'text-bad'}`}>
                {i.status === 'free' ? `бесплатно до ${when(i.free_until!)}` : i.status === 'nonrefundable' ? 'невозвратно' : `штраф ${rub(i.penalty_rub)}`}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-ink-muted">Расчёт — по условиям отмены каждого предложения; отмена в прототипе не выполняется.</p>
      </section>
    </div>
  )
}

function Timeline({ reminders }: { reminders: Reminder[] }) {
  const next = reminders.find((r) => !r.past)
  return (
    <section className="card p-4">
      <h3 className="mb-3 font-semibold">Что дальше</h3>
      <ol className="space-y-2.5">
        {reminders.map((r, i) => (
          <li key={r.at + r.kind + i} className={`flex gap-3 text-sm ${r.past ? 'opacity-40' : ''}`}>
            <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl ${r === next ? 'bg-brand text-white' : r.kind === 'free_cancel_deadline' ? 'bg-warn-bg text-warn' : 'bg-brand-light text-brand'}`}>
              <Icon name={r.kind === 'departure' && r.offer_kind === 'train' ? 'train' : REMINDER_ICON[r.kind]} className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs text-ink-muted">{when(r.at)}{r === next && ' · следующее'}</span>
              <span className="block truncate">{r.text}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function DateShift({ orderId, onDone }: { orderId: string; onDone: () => void }) {
  const [days, setDays] = useState<number | null>(null)
  const [quote, setQuote] = useState<DateShiftQuote | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // один ключ на расчёт: двойное нажатие «Подтвердить» не создаст второе изменение
  const keyRef = useRef('')

  const ask = async (d: number) => {
    setDays(d)
    setQuote(null)
    setError(null)
    setBusy(true)
    try {
      setQuote(await api.quoteDateShift(orderId, d))
      keyRef.current = `shift-${uuid()}`
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось пересчитать — попробуйте ещё раз')
    } finally {
      setBusy(false)
    }
  }
  const confirm = async () => {
    if (!quote || days == null) return
    setBusy(true)
    try {
      await api.confirmDateShift(orderId, days, keyRef.current)
      setQuote(null)
      setDays(null)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось подтвердить — попробуйте ещё раз')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card p-4">
      <h3 className="font-semibold">Сдвинуть даты</h3>
      <p className="text-sm text-ink-grey">Агент пересоберёт поездку: оставит, что подходит, заменит остальное и посчитает штрафы.</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {[-2, -1, 1, 2, 3].map((d) => (
          <button
            key={d}
            onClick={() => ask(d)}
            disabled={busy}
            className={`rounded-xl px-3.5 py-2 text-sm font-medium transition ${days === d ? 'bg-brand text-white' : 'bg-canvas text-ink hover:bg-brand-light'}`}
          >
            {d > 0 ? `+${d}` : d} {Math.abs(d) === 1 ? 'день' : 'дня'}
          </button>
        ))}
      </div>
      {busy && !quote && <div className="mt-3 text-sm text-ink-muted">Пересобираю поездку на новые даты…</div>}
      {error && <div className="mt-3 rounded-xl bg-bad-bg p-3 text-sm text-bad">{error}</div>}
      {quote && !quote.feasible && (
        <div className="mt-3 rounded-xl bg-warn-bg p-3 text-sm text-[#8a4b00]">
          На {day(quote.new_dates.start)} — {day(quote.new_dates.end)} поездка не собирается. {quote.notes?.join(' ')}
        </div>
      )}
      {quote?.feasible && (
        <div className="mt-3 space-y-2 rounded-2xl bg-canvas p-3 text-sm">
          <div className="font-medium">
            Новые даты: {day(quote.new_dates.start)} — {day(quote.new_dates.end)}
          </div>
          {!!quote.kept?.length && (
            <div><span className="text-ok">Сохранится:</span> {quote.kept.map((x) => x.title).join('; ')}</div>
          )}
          {!!quote.replaced?.length && (
            <div>
              <span className="text-warn">Заменится:</span>{' '}
              {quote.replaced.map((x) => (x.same_item ? `${x.title} (тот же, новые даты)` : x.title)).join('; ')}
            </div>
          )}
          <div className="space-y-0.5 border-t border-black/[.06] pt-2">
            <Row label="Новая стоимость поездки" value={rub(quote.new_total_rub!)} />
            <Row label="Разница" value={`${quote.price_delta_rub! > 0 ? '+' : ''}${rub(quote.price_delta_rub!)}`} />
            {!!quote.penalty_total_rub && <Row label="Штрафы за отмену прежних позиций" value={rub(quote.penalty_total_rub)} bad />}
            <Row label={quote.to_pay_rub! >= 0 ? 'К доплате' : 'К возврату'} value={rub(Math.abs(quote.to_pay_rub!))} strong />
          </div>
          <button onClick={confirm} disabled={busy} className="btn-primary w-full">
            {busy ? 'Подтверждаю…' : quote.to_pay_rub! > 0 ? `Подтвердить и доплатить ${rub(quote.to_pay_rub!)}` : 'Подтвердить изменение'}
          </button>
        </div>
      )}
    </section>
  )
}

function Row({ label, value, bad, strong }: { label: string; value: string; bad?: boolean; strong?: boolean }) {
  return (
    <div className={`flex justify-between ${strong ? 'pt-1 text-[15px] font-semibold' : 'text-ink-grey'}`}>
      <span>{label}</span>
      <span className={`tabular-nums ${bad ? 'text-bad' : ''}`}>{value}</span>
    </div>
  )
}

function EarlyCheckin({ orderId, option, done, onDone }: {
  orderId: string
  option: NonNullable<Awaited<ReturnType<typeof api.getAftercare>>['early_checkin']>
  done: boolean
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const keyRef = useRef(`ec-${uuid()}`)
  const arrival = option.arrival_at ? localParts(option.arrival_at).time : null
  const request = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.requestEarlyCheckin(orderId, keyRef.current)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось отправить запрос')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="card p-4">
      <h3 className="font-semibold">Ранний заезд</h3>
      <p className="text-sm text-ink-grey">
        {arrival && <>Вы приедете в отель около {arrival}, </>}стандартное заселение — с {option.standard_checkin}.
      </p>
      {option.available ? (
        <>
          <p className="mt-2 text-sm">
            Отель разрешает заезд с <b>{option.from}</b>
            {option.price_rub ? <> за {rub(option.price_rub)}</> : ' бесплатно'}
            {option.guaranteed ? ', с гарантией.' : ' — по наличию, отель подтвердит на месте.'}
          </p>
          {error && <div className="mt-2 rounded-xl bg-bad-bg p-2.5 text-sm text-bad">{error}</div>}
          <button onClick={request} disabled={busy || done} className="btn-soft mt-3 w-full">
            {done ? 'Запрос отправлен' : busy ? 'Отправляю…' : 'Запросить ранний заезд'}
          </button>
        </>
      ) : (
        <p className="mt-2 rounded-xl bg-canvas p-3 text-sm text-ink-grey">
          Этот отель ранний заезд не предоставляет.{' '}
          {option.luggage_storage ? `Вещи можно оставить в камере хранения до ${option.standard_checkin}.` : 'Камеры хранения нет — учтите это при планировании дня приезда.'}
        </p>
      )}
    </section>
  )
}
