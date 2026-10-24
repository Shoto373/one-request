import { Link, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, IS_MOCK } from './api/client'
import Logo from './components/Logo'
import TripPage from './pages/TripPage'
import CheckoutPage from './pages/CheckoutPage'
import OrderPage from './pages/OrderPage'

export default function App() {
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 60_000 })
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-4 border-b border-black/[.06] bg-white px-4 md:px-6">
        <Link to="/" aria-label="На главную">
          <Logo />
        </Link>
        <span className="hidden h-6 w-px bg-black/10 lg:block" />
        <span className="hidden text-sm text-ink-grey lg:block">Скажите, куда хотите. Остальное устроит агент.</span>
        <div className="ml-auto flex items-center gap-2">
          {status?.agent_mode === 'rules' && (
            <span className="rounded-full bg-brand-light px-3 py-1 text-xs font-semibold text-brand" title={status.message ?? undefined}>
              офлайн-режим · без LLM
            </span>
          )}
          {IS_MOCK && (
            <span className="rounded-full bg-warn-bg px-3 py-1 text-xs font-semibold text-[#8a4b00]" title="Фронт работает без бэкенда на вымышленных данных">
              моковые данные
            </span>
          )}
          <span className="hidden rounded-full bg-lime px-3 py-1 text-xs font-bold text-ink sm:inline">хакатон Островка</span>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<TripPage />} />
          <Route path="/checkout/:cartId" element={<CheckoutPage />} />
          <Route path="/orders/:id" element={<OrderPage />} />
          <Route
            path="*"
            element={
              <div className="p-8">
                Страница не найдена.{' '}
                <Link className="font-semibold text-brand" to="/">
                  На главную
                </Link>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  )
}
