import { Link, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, IS_MOCK } from './api/client'
import Logo from './components/Logo'
import TripPage from './pages/TripPage'
import CheckoutPage from './pages/CheckoutPage'
import OrderPage from './pages/OrderPage'

export default function App() {
  const home = useLocation().pathname === '/'
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 60_000 })
  return (
    <div className="flex h-full flex-col">
      <header className={`flex h-16 shrink-0 items-center gap-4 px-4 md:px-8 ${home ? 'bg-lime' : 'border-b border-black/[.06] bg-white'}`}>
        <Link to="/" aria-label="На главную">
          <Logo />
        </Link>
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
          <span className={`hidden rounded-xl px-3 py-1.5 text-xs font-medium sm:inline ${home ? 'bg-white/50 text-ink' : 'bg-lime-soft text-ink'}`}>хакатон Островка</span>
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
