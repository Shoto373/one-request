import { Link, Route, Routes } from 'react-router-dom'
import { IS_MOCK } from './api/client'
import TripPage from './pages/TripPage'
import CheckoutPage from './pages/CheckoutPage'
import OrderPage from './pages/OrderPage'

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b bg-white px-4 py-2.5">
        <Link to="/" className="flex items-center gap-2 font-semibold text-brand">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          О!дин запрос
        </Link>
        <span className="hidden text-sm text-slate-500 sm:inline">— скажите, куда хотите. Остальное устроит агент.</span>
        {IS_MOCK && <span className="ml-auto rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">моковые данные</span>}
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<TripPage />} />
          <Route path="/checkout/:cartId" element={<CheckoutPage />} />
          <Route path="/orders/:id" element={<OrderPage />} />
          <Route path="*" element={<div className="p-8">Страница не найдена. <Link className="text-brand underline" to="/">На главную</Link></div>} />
        </Routes>
      </main>
    </div>
  )
}
