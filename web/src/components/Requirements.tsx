import { useEffect, useRef, useState } from 'react'
import type { Requirement, RequirementKind } from '../api/types'
import { REQ_KIND, reqLabel } from '../lib/format'
import Icon from './Icon'

interface Props {
  requirements: Requirement[]
  busy: boolean
  onEdit: (r: Requirement, text: string) => void
  onRemove: (r: Requirement) => void
  onConfirm: (r: Requirement) => void
}

const ORDER: RequirementKind[] = ['hard', 'soft', 'assumption', 'unknown']

/** «Что агент понял»: факты отдельно от догадок; каждую можно подтвердить, исправить или убрать */
export default function Requirements({ requirements, busy, onEdit, onRemove, onConfirm }: Props) {
  const [editing, setEditing] = useState<string | null>(null)
  const sorted = [...requirements].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
  const assumptions = requirements.filter((r) => r.kind === 'assumption').length

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[17px] font-bold">Что агент понял</h2>
        {assumptions > 0 && (
          <span className="text-xs text-[#8a4b00]">
            {assumptions === 1 ? '1 догадка — проверьте' : `${assumptions} догадки — проверьте`}
          </span>
        )}
      </div>

      {!requirements.length ? (
        <p className="text-sm text-ink-muted">Пока ничего — напишите, какую поездку хотите.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {sorted.map((r) =>
            editing === r.key ? (
              <EditChip
                key={r.key}
                r={r}
                onCancel={() => setEditing(null)}
                onSave={(t) => {
                  setEditing(null)
                  onEdit(r, t)
                }}
              />
            ) : (
              <Chip
                key={r.key}
                r={r}
                busy={busy}
                onEdit={() => setEditing(r.key)}
                onRemove={() => onRemove(r)}
                onConfirm={() => onConfirm(r)}
              />
            ),
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-black/[.05] pt-3 text-[11px] text-ink-muted">
        {(['hard', 'soft', 'assumption'] as const).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5" title={REQ_KIND[k].hint}>
            <span className={`h-2 w-2 rounded-full ${REQ_KIND[k].dot}`} />
            {REQ_KIND[k].label}
          </span>
        ))}
      </div>
    </section>
  )
}

function Chip({ r, busy, onEdit, onRemove, onConfirm }: { r: Requirement; busy: boolean; onEdit: () => void; onRemove: () => void; onConfirm: () => void }) {
  const k = REQ_KIND[r.kind]
  return (
    <span
      className={`chip group py-1.5 pl-3 pr-1.5 text-[13px] ${k.cls}`}
      title={`${k.label}. ${k.hint}${r.quote ? `\nИз вашего сообщения: «${r.quote}»` : ''}`}
    >
      {reqLabel(r)}
      <span className="flex items-center">
        {r.kind === 'assumption' && (
          <button onClick={onConfirm} disabled={busy} className="rounded-full p-1 hover:bg-black/10 disabled:opacity-30" title="Да, так и есть">
            <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.4} />
          </button>
        )}
        <button onClick={onEdit} disabled={busy} className="rounded-full p-1 opacity-60 hover:bg-black/10 hover:opacity-100 disabled:opacity-20" title="Исправить">
          <Icon name="pencil" className="h-3.5 w-3.5" />
        </button>
        {r.kind !== 'hard' && (
          <button onClick={onRemove} disabled={busy} className="rounded-full p-1 opacity-60 hover:bg-black/10 hover:opacity-100 disabled:opacity-20" title="Убрать — агент перепроверит план">
            <Icon name="x" className="h-3.5 w-3.5" strokeWidth={2.2} />
          </button>
        )}
      </span>
    </span>
  )
}

function EditChip({ r, onSave, onCancel }: { r: Requirement; onSave: (t: string) => void; onCancel: () => void }) {
  const [v, setV] = useState(reqLabel(r))
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    ref.current?.select()
  }, [])
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (v.trim()) onSave(v.trim())
      }}
      className="chip border-brand bg-white py-1 pl-3 pr-1 shadow-lift"
    >
      <input
        ref={ref}
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && onCancel()}
        className="w-44 bg-transparent text-[13px] text-ink outline-none"
      />
      <button type="submit" className="rounded-full bg-brand p-1 text-white" title="Сохранить">
        <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.4} />
      </button>
      <button type="button" onClick={onCancel} className="rounded-full p-1 text-ink-muted hover:bg-black/5" title="Отмена">
        <Icon name="x" className="h-3.5 w-3.5" />
      </button>
    </form>
  )
}
