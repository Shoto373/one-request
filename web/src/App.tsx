import { Link, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, IS_MOCK } from './api/client'
import Logo from './components/Logo'
import TripPage from './pages/TripPage'
import CheckoutPage from './pages/CheckoutPage'
import OrderPage from './pages/OrderPage'

export default function App() {
  const [tripGeneration, setTripGeneration] = useState(0)
  const location = useLocation()
  const isHome = location.pathname === '/'
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 60_000 })
  return (
    <div className="flex h-full flex-col">
      <header className={`relative z-10 flex h-16 shrink-0 items-center gap-4 px-4 md:px-8 ${isHome ? 'border-transparent bg-lime' : 'border-b border-black/[.06] bg-white'}`}>
        <Link to="/" aria-label="На главную — начать новую поездку" title="На главную · новая поездка" onClick={() => setTripGeneration((n) => n + 1)}>
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
          <span className="hidden rounded-xl bg-lime-soft px-3 py-1.5 text-xs font-medium text-ink sm:inline">хакатон Островка</span>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<TripPage key={tripGeneration} freshStart={tripGeneration > 0} />} />
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
