import { useEffect, useRef, useState } from 'react'
import type { ChatMessage, OpenQuestion, TraceStep } from '../api/types'
import Icon from './Icon'
import { AgentAvatar } from './Logo'
import { STEP_LABEL } from './TracePanel'

const EXAMPLES = [
  { emoji: '🌊', text: 'Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов' },
  { emoji: '🚆', text: 'Из Москвы в Сочи 10–17 ноября, двое взрослых' },
  { emoji: '🏙️', text: 'Неделя в Дубае на троих за 30 000' },
]

interface Props {
  messages: ChatMessage[]
  steps: TraceStep[]
  questions: OpenQuestion[]
  busy: boolean
  disabled: boolean
  onSend: (text: string) => void
  onAnswer: (q: OpenQuestion, value: string) => void
}

export default function Chat({ messages, steps, questions, busy, disabled, onSend, onAnswer }: Props) {
  const [text, setText] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages, steps, questions])

  const submit = (t: string) => {
    const v = t.trim()
    if (!v || busy || disabled) return
    onSend(v)
    setText('')
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
        {messages.length === 0 ? (
          <Welcome disabled={disabled} onPick={submit} />
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-md bg-brand px-4 py-2.5 text-[15px] leading-snug text-white">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex items-end gap-2.5">
                  <AgentAvatar />
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-bl-md border border-black/[.06] bg-white px-4 py-2.5 text-[15px] leading-snug shadow-card">
                    {m.text}
                  </div>
                </div>
              ),
            )}

            {busy && <Thinking steps={steps} />}

            {!busy && questions.length > 0 && (
              <div className="ml-[42px] space-y-3">
                {questions.map((q) => (
                  <div key={q.key} className="rounded-2xl border border-brand/15 bg-brand-light/70 p-3.5">
                    <div className="text-sm font-semibold text-ink">{q.question}</div>
                    <div className="mt-0.5 text-xs text-ink-grey">{q.why}</div>
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {q.options?.map((o) => (
                        <button
                          key={o}
                          onClick={() => onAnswer(q, o)}
                          disabled={disabled}
                          className="rounded-full border border-brand/25 bg-white px-3.5 py-1.5 text-sm font-medium text-brand transition hover:border-brand hover:bg-brand hover:text-white"
                        >
                          {o}
                        </button>
                      ))}
                      <button
                        onClick={() => input.current?.focus()}
                        className="rounded-full px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
                      >
                        свой ответ…
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        className="border-t border-black/[.06] bg-white p-3"
        onSubmit={(e) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <div className="flex items-end gap-2 rounded-2xl border border-black/10 bg-canvas/60 p-1.5 pl-3.5 transition focus-within:border-brand focus-within:bg-white focus-within:shadow-lift">
          <textarea
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit(text)
              }
            }}
            rows={1}
            placeholder={questions.length ? 'Ответьте или уточните…' : 'Куда, когда, кто едет, что важно…'}
            className="max-h-36 min-h-[40px] min-w-0 flex-1 resize-none bg-transparent py-2 text-[15px] outline-none placeholder:text-ink-muted"
            disabled={disabled}
          />
          <button
            type="submit"
            disabled={busy || disabled || !text.trim()}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand text-white transition hover:bg-brand-hover disabled:bg-ink-line disabled:text-ink-muted"
            aria-label="Отправить"
          >
            <Icon name="send" className="h-5 w-5" strokeWidth={2.2} />
          </button>
        </div>
      </form>
    </div>
  )
}

function Welcome({ disabled, onPick }: { disabled: boolean; onPick: (t: string) => void }) {
  return (
    <div className="flex h-full flex-col justify-end gap-5 pb-2">
      <div className="guru-gradient relative overflow-hidden rounded-3xl p-6 text-white">
        <div className="absolute -right-6 -top-8 h-32 w-32 rounded-full bg-lime/90 blur-2xl" aria-hidden />
        <div className="relative">
          <div className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-white/15 px-2.5 py-1 text-xs font-medium backdrop-blur">
            <Icon name="sparkle" className="h-3.5 w-3.5" /> AI-агент
          </div>
          <h1 className="text-[26px] font-bold leading-[1.1] tracking-tight">
            Скажите, куда хотите.
            <br />
            Остальное устроит агент.
          </h1>
          <p className="mt-2 text-sm text-white/80">
            Опишите поездку своими словами — агент уточнит главное, сравнит варианты, проверит стыковки и соберёт всё в один заказ.
          </p>
        </div>
      </div>
      <div>
        <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Попробуйте</div>
        <div className="flex flex-col gap-2">
          {EXAMPLES.map((e) => (
            <button
              key={e.text}
              onClick={() => onPick(e.text)}
              disabled={disabled}
              className="group flex items-center gap-3 rounded-2xl border border-black/[.06] bg-white px-3.5 py-3 text-left text-sm shadow-card transition hover:border-brand/40 hover:shadow-lift disabled:opacity-50"
            >
              <span className="text-lg">{e.emoji}</span>
              <span className="flex-1">{e.text}</span>
              <Icon name="chevron" className="h-4 w-4 text-ink-muted transition group-hover:translate-x-0.5 group-hover:text-brand" />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function Thinking({ steps }: { steps: TraceStep[] }) {
  return (
    <div className="flex items-start gap-2.5">
      <AgentAvatar />
      <div className="rounded-3xl rounded-bl-md border border-black/[.06] bg-white px-4 py-3 shadow-card">
        <div className="flex items-center gap-2 text-sm font-medium text-ink-grey">
          <span className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand" style={{ animationDelay: `${i * 120}ms` }} />
            ))}
          </span>
          Агент работает
        </div>
        {steps.length > 0 && (
          <ul className="mt-2 space-y-1">
            {steps.map((s, i) => (
              <li key={i} className="flex items-center gap-2 text-xs text-ink-grey">
                <Icon name={s.status === 'error' ? 'x' : 'check'} className={`h-3.5 w-3.5 ${s.status === 'error' ? 'text-bad' : 'text-ok'}`} strokeWidth={2.4} />
                {STEP_LABEL[s.name] ?? s.name}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
