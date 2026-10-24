/** Знак продукта в духе «О!» Островка: синий круг, восклицательный акцент салатовым. */
export function Mark({ className = 'h-9 w-9' }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden>
      <circle cx="20" cy="20" r="20" fill="#0d41d2" />
      <circle cx="17" cy="20" r="8.2" fill="none" stroke="#fff" strokeWidth="4.2" />
      <rect x="28.6" y="10.5" width="4.2" height="12.5" rx="2.1" fill="#acef81" />
      <circle cx="30.7" cy="28.2" r="2.4" fill="#acef81" />
    </svg>
  )
}

/** Аватар агента — градиент AI-ассистента Островка */
export function AgentAvatar({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <div className={`guru-gradient grid shrink-0 place-items-center rounded-full text-white ${className}`} aria-hidden>
      <svg viewBox="0 0 24 24" className="h-[55%] w-[55%]" fill="currentColor">
        <path d="M12 2.5l2.1 5.4 5.4 2.1-5.4 2.1L12 17.5l-2.1-5.4L4.5 10l5.4-2.1zM18.5 15l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z" />
      </svg>
    </div>
  )
}

export default function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <Mark />
      <span className="leading-none">
        <span className="block font-display text-[18px] font-bold tracking-tight text-brand">О!дин запрос</span>
        <span className="block text-[11px] font-medium text-ink-grey">AI-агент путешествий</span>
      </span>
    </span>
  )
}
