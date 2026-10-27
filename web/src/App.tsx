import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, IS_MOCK } from './api/client'
import Logo from './components/Logo'
import HomePage from './pages/HomePage'
import TripPage from './pages/TripPage'
import CheckoutPage from './pages/CheckoutPage'
import OrderPage from './pages/OrderPage'

export default function App() {
  const [tripGeneration, setTripGeneration] = useState(0)
  const location = useLocation()
  const navigate = useNavigate()
  const isHome = location.pathname === '/'
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 60_000 })

  const handleNewTrip = () => {
    setTripGeneration((n) => n + 1)
    navigate('/trip')
  }

  return (
    <div className="flex h-full flex-col">
      <header
        className={`relative z-10 flex h-16 shrink-0 items-center gap-4 px-4 md:px-8 ${
          isHome ? 'border-b border-black/[.06] bg-lime' : 'border-b border-black/[.06] bg-white'
        }`}
      >
        <Link to="/" aria-label="На главную" title="На главную">
          <Logo />
        </Link>

        {/* Навигация между главной и чатом с агентом */}
        <nav className="flex items-center gap-1.5 ml-2 md:ml-4">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `rounded-xl px-3 py-1.5 text-xs md:text-sm font-semibold transition-colors ${
                isActive
                  ? 'bg-black text-white shadow-sm'
                  : 'text-ink-grey hover:bg-black/5 hover:text-ink'
              }`
            }
          >
            Главная
          </NavLink>
          <NavLink
            to="/trip"
            className={({ isActive }) =>
              `rounded-xl px-3 py-1.5 text-xs md:text-sm font-semibold transition-colors ${
                isActive
                  ? 'bg-brand text-white shadow-sm'
                  : 'text-ink-grey hover:bg-black/5 hover:text-ink'
              }`
            }
          >
            Чат с агентом
          </NavLink>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleNewTrip}
            className="flex items-center gap-1 rounded-xl bg-ink-cream px-3 py-1.5 text-xs font-semibold text-ink hover:bg-black/10 transition-colors"
            title="Начать новую поездку и очистить контекст"
          >
            <span>+ Новая поездка</span>
          </button>

          {status?.agent_mode === 'rules' && (
            <span
              className="rounded-full bg-brand-light px-3 py-1 text-xs font-semibold text-brand"
              title={status.message ?? undefined}
            >
              офлайн-режим · без LLM
            </span>
          )}
          {IS_MOCK && (
            <span
              className="rounded-full bg-warn-bg px-3 py-1 text-xs font-semibold text-[#8a4b00]"
              title="Фронт работает без бэкенда на вымышленных данных"
            >
              моковые данные
            </span>
          )}
          <span className="hidden rounded-xl bg-lime-soft px-3 py-1.5 text-xs font-medium text-ink sm:inline">
            хакатон Островка
          </span>
        </div>
      </header>

      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/trip" element={<TripPage key={tripGeneration} freshStart={tripGeneration > 0} />} />
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
