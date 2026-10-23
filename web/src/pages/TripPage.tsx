import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import Chat from '../components/Chat'
import TripCanvas from '../components/TripCanvas'
import { useTripSession } from '../lib/useTripSession'

export default function TripPage() {
  const navigate = useNavigate()
  const s = useTripSession()
  const [checkoutBusy, setCheckoutBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<'chat' | 'trip'>('chat')

  const toCart = async (bundleId: string) => {
    if (!s.state || !s.sessionId) return
    setCheckoutBusy(bundleId)
    try {
      const cart = await api.createCart({ session_id: s.sessionId, version: s.state.version, bundle_id: bundleId })
      navigate(`/checkout/${cart.cart_id}`)
    } catch (e) {
      s.setError(e instanceof ApiError ? e.message : 'Не удалось оформить')
    } finally {
      setCheckoutBusy(null)
    }
  }

  return (
    <div className="flex h-full flex-col">
      {s.error && (
        <div className="flex items-center justify-between gap-3 border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">
          <span>{s.error}</span>
          <button onClick={() => s.setError(null)} className="text-rose-500 hover:text-rose-800">
            ×
          </button>
        </div>
      )}

      {/* мобильные вкладки */}
      <div className="flex border-b bg-white text-sm md:hidden">
        {(['chat', 'trip'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-2 ${tab === t ? 'border-b-2 border-brand font-medium text-brand' : 'text-slate-500'}`}
          >
            {t === 'chat' ? 'Чат' : `Поездка${s.state?.bundles.length ? ` (${s.state.bundles.length})` : ''}`}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1">
        <section className={`${tab === 'chat' ? 'flex' : 'hidden'} w-full flex-col border-r bg-slate-50 md:flex md:w-[420px] md:shrink-0`}>
          <div className="flex items-center justify-between border-b bg-white px-4 py-2 text-xs text-slate-500">
            <span>Диалог с агентом</span>
            <button onClick={s.restart} disabled={s.busy} className="hover:text-brand disabled:opacity-40">
              Новая поездка
            </button>
          </div>
          <div className="min-h-0 flex-1">
            <Chat messages={s.messages} steps={s.steps} busy={s.busy} disabled={!s.sessionId} onSend={s.send} />
          </div>
        </section>

        <section className={`${tab === 'trip' ? 'block' : 'hidden'} min-w-0 flex-1 md:block`}>
          <TripCanvas
            state={s.state}
            busy={s.busy}
            checkoutBusyId={checkoutBusy}
            onRemoveRequirement={(key) => s.command('remove_requirement', { key })}
            onCheckout={toCart}
          />
        </section>
      </div>
    </div>
  )
}
