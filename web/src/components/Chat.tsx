import { useEffect, useRef, useState } from 'react'
import type { ChatMessage, TraceStep } from '../api/types'

const EXAMPLES = [
  'Из Москвы в Сочи 10–17 ноября, двое взрослых',
  'Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов',
  'Из Москвы в Казань на выходные поездом',
]

interface Props {
  messages: ChatMessage[]
  steps: TraceStep[]
  busy: boolean
  disabled: boolean
  onSend: (text: string) => void
}

export default function Chat({ messages, steps, busy, disabled, onSend }: Props) {
  const [text, setText] = useState('')
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages, steps])

  const submit = (t: string) => {
    const v = t.trim()
    if (!v || busy || disabled) return
    onSend(v)
    setText('')
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-3 pt-6 text-sm text-slate-600">
            <p className="text-base font-medium text-slate-800">Опишите поездку своими словами — агент соберёт её целиком.</p>
            <p>Например:</p>
            <div className="flex flex-col gap-2">
              {EXAMPLES.map((e) => (
                <button
                  key={e}
                  onClick={() => submit(e)}
                  disabled={disabled}
                  className="rounded-lg border bg-white px-3 py-2 text-left hover:border-brand hover:text-brand disabled:opacity-50"
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
                m.role === 'user' ? 'rounded-br-sm bg-brand text-white' : 'rounded-bl-sm border bg-white'
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}

        {busy && (
          <div className="rounded-lg border border-dashed bg-white/60 p-2.5 text-xs text-slate-500">
            <div className="mb-1 font-medium text-slate-600">Агент работает…</div>
            {steps.map((s, i) => (
              <div key={i} className={`font-mono ${s.status === 'ok' ? '' : 'text-rose-600'}`}>
                → {s.name}
              </div>
            ))}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        className="flex gap-2 border-t bg-white p-3"
        onSubmit={(e) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit(text)
            }
          }}
          rows={2}
          placeholder="Куда, когда, кто едет, что важно…"
          className="min-w-0 flex-1 resize-none rounded-lg border px-3 py-2 text-sm outline-none focus:border-brand"
          disabled={disabled}
        />
        <button
          type="submit"
          disabled={busy || disabled || !text.trim()}
          className="rounded-lg bg-brand px-4 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40"
        >
          Отправить
        </button>
      </form>
    </div>
  )
}
