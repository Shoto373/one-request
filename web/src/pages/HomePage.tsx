import { useNavigate } from 'react-router-dom'
import Landing from '../components/Landing'

export default function HomePage() {
  const navigate = useNavigate()

  const handleSend = (text: string) => {
    navigate('/trip', { state: { initialQuery: text } })
  }

  return (
    <div className="h-full overflow-y-auto">
      <Landing disabled={false} connecting={false} onSend={handleSend} />
    </div>
  )
}
