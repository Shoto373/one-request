import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ChatMessage, CommandIn, StreamEvent, TraceStep, TripState } from '../api/types'
import { diffPlans, type PlanDiff } from './diff'
import { profileId } from './profile'

const KEY = 'trip_session_id'
/** как часто сверяем версию плана, пока вкладка видна и агент не занят */
const SYNC_MS = 15000

/** Понятные тексты для кодов ошибок агента (app/main.py) */
const ERROR_TEXT: Record<string, string> = {
  llm_not_configured: 'Агенту не задан ключ LLM (LLM_API_KEY в .env) — понять запрос пока нечем.',
  llm_request_failed: 'LLM не ответила — попробуйте ещё раз.',
  invalid_llm_response: 'LLM вернула некорректный ответ — попробуйте ещё раз или переформулируйте.',
  catalog_integration_missing: 'Агент понял запрос, но каталог поездок не подключён.',
  a_tool_unavailable: 'Инструмент расчёта поездки недоступен — попробуйте ещё раз.',
  agent_step_limit: 'Агент не уложился в лимит шагов — уточните запрос.',
  invalid_command: 'Это действие сейчас недоступно — план мог измениться.',
  request_id_conflict: 'Повтор запроса не совпал с исходным. Обновите состояние и отправьте новое действие.',
  request_in_progress: 'Запрос ещё выполняется.',
  agent_error: 'Агент не смог обработать запрос.',
  database_unavailable: 'База данных недоступна.',
  version_conflict: 'План успел измениться — показываю актуальную версию. Проверьте и повторите действие.',
  stream_cut: 'Связь с агентом прервалась до ответа.',
  network: 'Нет связи с сервером — проверьте подключение.',
}

/** Сообщение в ленте; failed — не дошло до агента, можно повторить */
export type UiMessage = ChatMessage & { failed?: boolean }

export interface Notice {
  tone: 'error' | 'info'
  text: string
  /** есть что повторить — в баннере кнопка «Повторить» */
  retry?: boolean
}

/** ошибка хода агента: пришла событием error в SSE или поток оборвался без done */
class TurnError extends Error {
  constructor(public code: string, message: string) {
    super(message)
  }
}

function describe(e: unknown): string {
  if (e instanceof ApiError) return ERROR_TEXT[e.detail.code] ?? e.message
  if (e instanceof TurnError) return ERROR_TEXT[e.code] ?? e.message
  if (e instanceof TypeError) return ERROR_TEXT.network // fetch: сеть/сервер недоступны
  return e instanceof Error ? e.message : String(e)
}

const isConflict = (e: unknown) =>
  (e instanceof ApiError && e.status === 409) || (e instanceof TurnError && e.code === 'version_conflict')
const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError'

function stored(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
function store(id: string | null) {
  try {
    if (id) localStorage.setItem(KEY, id)
    else localStorage.removeItem(KEY)
  } catch {
    /* приватный режим */
  }
}

/** Команду бэкенд не знает (нет эндпоинта или типа) → действие уходит обычным сообщением в чат */
function unsupported(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.detail.code === 'session_not_found') return false
  return e.status === 404 || e.status === 405 || (e.status === 422 && e.detail.code === 'validation')
}

type Op = (signal: AbortSignal) => Promise<void>
type Send = (version: number, onEvent: (e: StreamEvent) => void, signal: AbortSignal) => Promise<void>

/**
 * Сессия поездки: создать/восстановить, отправить сообщение или команду (SSE), держать актуальный TripState.
 * Состояние меняет только агент — мы шлём expected_version. На 409 перечитываем сессию:
 * сообщение переотправляем к новой версии сами (намерение пользователя не зависит от версии),
 * команду — нет (её id могли исчезнуть после пересборки).
 */
export function useTripSession() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [state, setState] = useState<TripState | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  /** шаги текущего хода (живые) */
  const [steps, setSteps] = useState<TraceStep[]>([])
  /** вся история шагов сессии (GET /trace + живые) */
  const [trace, setTrace] = useState<TraceStep[]>([])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  /** последняя пересборка: что сохранено, что заменено */
  const [diff, setDiff] = useState<PlanDiff | null>(null)
  const versionRef = useRef(0)
  const stateRef = useRef<TripState | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  /** синхронный флаг «идёт ход»: двойной клик не отправит второй запрос */
  const busyRef = useRef(false)
  /** что повторить по кнопке в баннере */
  const retryRef = useRef<(() => void) | null>(null)

  const apply = useCallback((s: TripState) => {
    const prev = stateRef.current
    if (prev && prev.session_id === s.session_id && prev.version !== s.version) {
      const d = diffPlans(prev, s)
      if (d) setDiff(d)
      else if (!s.bundles.length) setDiff(null)
    }
    stateRef.current = s
    versionRef.current = s.version
    setState(s)
  }, [])

  const fail = useCallback((text: string, retry?: () => void) => {
    retryRef.current = retry ?? null
    setNotice({ tone: 'error', text, retry: !!retry })
  }, [])

  const loadTrace = useCallback(async (id: string) => {
    try {
      setTrace((await api.getTrace(id)).steps)
    } catch {
      /* трейс вторичен — не мешаем основному сценарию */
    }
  }, [])

  const reload = useCallback(async (id: string) => {
    const v = await api.getSession(id)
    apply(v.state)
    setMessages(v.messages)
    loadTrace(id)
  }, [apply, loadTrace])

  const start = useCallback(async (fresh = false) => {
    abortRef.current?.abort()
    setNotice(null)
    setSessionId(null)
    try {
      const existing = fresh ? null : stored()
      if (existing) {
        try {
          await reload(existing)
          setSessionId(existing)
          return
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) throw e
        }
      }
      // профиль браузера: агент подставит предпочтения из прошлых поездок догадками
      const created = await api.createSession(profileId())
      store(created.session_id)
      versionRef.current = created.version
      stateRef.current = null
      setState(null)
      setMessages([])
      setSteps([])
      setTrace([])
      setDiff(null)
      await reload(created.session_id).catch(() => undefined)
      setSessionId(created.session_id)
    } catch (e) {
      fail(
        e instanceof ApiError ? `Агент недоступен: ${describe(e)}` : 'Агент недоступен — бэкенд ещё не отвечает.',
        () => start(fresh),
      )
    }
  }, [reload, fail])

  useEffect(() => {
    start()
    return () => abortRef.current?.abort()
  }, [start])

  /**
   * Сверка с сервером, пока пользователь ничего не делает: план могли поменять в другой вкладке
   * или ход дошёл до конца уже после обрыва связи. Ловим это до того, как пользователь нажмёт кнопку и получит 409.
   */
  useEffect(() => {
    if (!sessionId) return
    const check = async () => {
      if (busyRef.current || document.visibilityState !== 'visible') return
      try {
        const v = await api.getSession(sessionId)
        if (busyRef.current || v.state.version <= versionRef.current) return
        apply(v.state)
        setMessages(v.messages)
        loadTrace(sessionId)
        retryRef.current = null
        setNotice({ tone: 'info', text: 'План обновился в другой вкладке — показываю актуальную версию.' })
      } catch {
        /* сервер недоступен — скажем об этом, когда пользователь что-то сделает */
      }
    }
    const t = setInterval(check, SYNC_MS)
    window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', check)
    return () => {
      clearInterval(t)
      window.removeEventListener('focus', check)
      document.removeEventListener('visibilitychange', check)
    }
  }, [sessionId, apply, loadTrace])

  /**
   * Один ход агента. Ошибки из потока превращаются в исключения; поток оборвался без done —
   * перечитываем сессию: если версия выросла, ход всё же сохранился.
   * resend: на 409 перечитать и отправить ещё раз к новой версии (один раз).
   */
  const turn = useCallback(async (send: Send, signal: AbortSignal, resend: boolean) => {
    const id = sessionId!
    for (let attempt = 0; ; attempt++) {
      // флаги хода меняет onEvent — держим в объекте, а не в let (иначе TS сузит их до начальных значений)
      const r = { done: false, failure: null as TurnError | null }
      const onEvent = (e: StreamEvent) => {
        switch (e.event) {
          case 'step':
            setSteps((s) => [...s, e.data])
            setTrace((t) => [...t, e.data])
            break
          case 'state':
            apply(e.data.state)
            break
          case 'message':
            setMessages((m) => [...m, { role: e.data.role, text: e.data.text }])
            break
          case 'replay':
            // The backend confirms this request_id was already accepted. SSE replay is
            // not available, so reload the authoritative session snapshot.
            void reload(id)
            break
          case 'error':
            r.failure = new TurnError(e.data.code, e.data.message)
            break
          case 'done':
            r.done = true
            break
        }
      }
      const before = versionRef.current
      try {
        await send(before, onEvent, signal)
        if (r.failure) throw r.failure
        if (!r.done) {
          await reload(id)
          if (versionRef.current > before) return
          throw new TurnError('stream_cut', 'stream ended without done')
        }
        return
      } catch (e) {
        if (!isConflict(e)) throw e
        await reload(id)
        if (!resend || attempt > 0) throw e
        setSteps([])
      }
    }
  }, [sessionId, apply, reload])

  const run = useCallback(async (op: Op): Promise<boolean> => {
    if (!sessionId || busyRef.current) return false
    busyRef.current = true
    setBusy(true)
    setNotice(null)
    setSteps([])
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await op(ctrl.signal)
      return true
    } catch (e) {
      if (!isAbort(e)) {
        if (e instanceof ApiError && e.status === 409 && e.detail.code === 'request_in_progress' && typeof e.detail.request_id === 'string') {
          const requestId = e.detail.request_id
          let status: Awaited<ReturnType<typeof api.getRequestStatus>> | null = null
          for (let attempt = 0; attempt < 12; attempt++) {
            status = await api.getRequestStatus(sessionId, requestId).catch(() => null)
            if (!status || status.status === 'not_found' || status.status === 'completed' || status.status === 'failed') break
            await new Promise((resolve) => window.setTimeout(resolve, 250))
          }
          await reload(sessionId).catch(() => undefined)
          if (status?.status === 'failed') fail(ERROR_TEXT[status.error?.code ?? ''] ?? status.error?.message ?? 'Запрос завершился ошибкой. Состояние обновлено.')
          else if (status?.status === 'completed') fail('Запрос уже выполнен — состояние обновлено. Проверьте актуальный план.')
          else fail('Запрос ещё выполняется или его статус недоступен. Состояние перечитано; проверьте результат перед повтором.')
        } else {
        // конфликт команды: план уже перечитан — повторять ту же команду вслепую не предлагаем
          if (isConflict(e)) {
            await reload(sessionId).catch(() => undefined)
            fail(describe(e))
          } else fail(describe(e), () => run(op))
        }
      }
      return false
    } finally {
      busyRef.current = false
      setBusy(false)
      loadTrace(sessionId)
    }
  }, [sessionId, loadTrace, reload, fail])

  /** сообщение в чат; не дошло — помечаем «не отправлено», «Повторить» отправит его снова */
  const chat = useCallback(async (text: string, requestId: string, signal: AbortSignal) => {
    try {
      await turn((version, onEvent, sig) => {
        // после reload лента уже с сервера — добавляем своё сообщение заново
        setMessages((m) => [...m.filter((x) => !x.failed), { role: 'user', text }])
        return api.sendMessage(sessionId!, { request_id: requestId, text, expected_version: version }, onEvent, sig)
      }, signal, false)
    } catch (e) {
      setMessages((m) => {
        const i = m.map((x) => x.role === 'user' && x.text === text).lastIndexOf(true)
        return i < 0 ? [...m, { role: 'user', text, failed: true }] : m.map((x, j) => (j === i ? { ...x, failed: true } : x))
      })
      throw e
    }
  }, [sessionId, turn])

  // request_id создаём на каждый запуск операции: «Повторить» после ошибки — это новый запрос,
  // старый id бэкенд помнит как failed и вернул бы ту же ошибку. Сетевой повтор внутри stream() шлёт тот же id.
  const send = useCallback((text: string) => run((signal) => chat(text, crypto.randomUUID(), signal)), [run, chat])

  /**
   * Действие из интерфейса (выбор направления, быстрый ответ, правка требования).
   * Сначала команда агенту; если бэкенд её ещё не знает — то же самое словами в чат.
   */
  const act = useCallback((type: CommandIn['type'], payload: CommandIn['payload'], asText: string) =>
    run(async (signal) => {
      const requestId = crypto.randomUUID()
      try {
        await turn((version, onEvent, sig) =>
          api.sendCommand(sessionId!, { request_id: requestId, type, payload, expected_version: version }, onEvent, sig), signal, false)
      } catch (e) {
        if (!unsupported(e)) throw e
        await chat(asText, crypto.randomUUID(), signal)
      }
    }), [run, turn, sessionId, chat])

  return {
    sessionId, state, messages, steps, trace, busy, notice, diff,
    /** сессия ещё не создана/не восстановлена и ошибки нет */
    connecting: !sessionId && !notice,
    clearDiff: () => setDiff(null),
    send, act,
    restart: () => start(true),
    retry: () => {
      const r = retryRef.current
      retryRef.current = null
      setNotice(null)
      r?.()
    },
    refresh: async () => {
      if (sessionId) await reload(sessionId).catch(() => undefined)
    },
    setError: (text: string | null) => (text ? fail(text) : setNotice(null)),
  }
}
