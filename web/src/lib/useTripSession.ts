import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ChatMessage, CommandIn, StreamEvent, TraceStep, TripState } from '../api/types'
import { diffPlans, type PlanDiff } from './diff'

const KEY = 'trip_session_id'

/** Понятные тексты для кодов ошибок агента (app/main.py) */
const ERROR_TEXT: Record<string, string> = {
  llm_not_configured: 'Агенту не задан ключ LLM (LLM_API_KEY в .env) — понять запрос пока нечем.',
  llm_request_failed: 'LLM не ответила — попробуйте ещё раз.',
  invalid_llm_response: 'LLM вернула некорректный ответ — попробуйте переформулировать.',
  catalog_integration_missing: 'Агент понял запрос, но каталог поездок не подключён.',
  a_tool_unavailable: 'Инструмент расчёта поездки недоступен — попробуйте ещё раз.',
  agent_step_limit: 'Агент не уложился в лимит шагов — уточните запрос.',
  invalid_command: 'Это действие сейчас недоступно — план мог измениться.',
  request_id_conflict: 'Повтор запроса не совпал с исходным. Обновите состояние и отправьте новое действие.',
  request_in_progress: 'Запрос ещё выполняется.',
  agent_error: 'Агент не смог обработать запрос.',
  database_unavailable: 'База данных недоступна.',
}

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

/**
 * Сессия поездки: создать/восстановить, отправить сообщение или команду (SSE), держать актуальный TripState.
 * Состояние меняет только агент — мы шлём expected_version; на 409 перечитываем сессию.
 */
export function useTripSession() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [state, setState] = useState<TripState | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  /** шаги текущего хода (живые) */
  const [steps, setSteps] = useState<TraceStep[]>([])
  /** вся история шагов сессии (GET /trace + живые) */
  const [trace, setTrace] = useState<TraceStep[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** последняя пересборка: что сохранено, что заменено */
  const [diff, setDiff] = useState<PlanDiff | null>(null)
  const versionRef = useRef(0)
  const stateRef = useRef<TripState | null>(null)
  const abortRef = useRef<AbortController | null>(null)

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
    setError(null)
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
      const created = await api.createSession()
      store(created.session_id)
      setSessionId(created.session_id)
      versionRef.current = created.version
      setMessages([])
      setSteps([])
      setTrace([])
      setDiff(null)
      stateRef.current = null
      await reload(created.session_id).catch(() => undefined)
    } catch (e) {
      setError(e instanceof ApiError ? `Агент недоступен: ${e.message}` : 'Агент недоступен — бэкенд ещё не отвечает')
    }
  }, [reload])

  useEffect(() => {
    start()
    return () => abortRef.current?.abort()
  }, [start])

  const onEvent = useCallback((e: StreamEvent) => {
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
        if (sessionId) void reload(sessionId)
        break
      case 'error':
        setError(ERROR_TEXT[e.data.code] ?? e.data.message)
        break
      case 'done':
        break
    }
  }, [apply, reload, sessionId])

  const run = useCallback(async (fn: (signal: AbortSignal) => Promise<void>) => {
    if (!sessionId) return
    setBusy(true)
    setError(null)
    setSteps([])
    abortRef.current = new AbortController()
    try {
      await fn(abortRef.current.signal)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.detail.code === 'request_in_progress' && typeof e.detail.request_id === 'string') {
        const requestId = e.detail.request_id
        let settled = false
        for (let attempt = 0; attempt < 16; attempt++) {
          const requestStatus = await api.getRequestStatus(sessionId, requestId).catch(() => null)
          if (requestStatus?.status === 'completed') {
            await reload(sessionId).catch(() => undefined)
            setError('Запрос выполнен — состояние обновлено. Проверьте актуальный план.')
            settled = true
            break
          }
          if (requestStatus?.status === 'failed') {
            await reload(sessionId).catch(() => undefined)
            setError(ERROR_TEXT[requestStatus.error?.code ?? ''] ?? requestStatus.error?.message ?? 'Запрос завершился ошибкой. Состояние обновлено.')
            settled = true
            break
          }
          if (!requestStatus || requestStatus.status === 'not_found') break
          await new Promise((resolve) => window.setTimeout(resolve, 250))
        }
        if (!settled) {
          await reload(sessionId).catch(() => undefined)
          setError('Запрос ещё выполняется. Состояние перечитано; не отправляйте это изменение повторно, пока не проверите результат.')
        }
      } else if (e instanceof ApiError && e.status === 409) {
        await reload(sessionId)
        setError('План обновился в другой вкладке — показываю актуальную версию. Повторите действие.')
      } else if (e instanceof ApiError) {
        setError(ERROR_TEXT[e.detail.code] ?? e.message)
      } else if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setBusy(false)
      loadTrace(sessionId)
    }
  }, [sessionId, reload, loadTrace])

  const chat = useCallback(async (text: string, signal: AbortSignal) => {
    setMessages((m) => [...m, { role: 'user', text }])
    await api.sendMessage(sessionId!, { request_id: crypto.randomUUID(), text, expected_version: versionRef.current }, onEvent, signal)
  }, [sessionId, onEvent])

  const send = useCallback((text: string) => run((signal) => chat(text, signal)), [run, chat])

  /**
   * Действие из интерфейса (выбор направления, быстрый ответ, правка требования).
   * Сначала команда агенту; если бэкенд её ещё не знает — то же самое словами в чат.
   */
  const act = useCallback((type: CommandIn['type'], payload: CommandIn['payload'], asText: string) =>
    run(async (signal) => {
      try {
        await api.sendCommand(sessionId!, { request_id: crypto.randomUUID(), type, payload, expected_version: versionRef.current }, onEvent, signal)
      } catch (e) {
        if (!unsupported(e)) throw e
        await chat(asText, signal)
      }
    }), [run, sessionId, onEvent, chat])

  return {
    sessionId, state, messages, steps, trace, busy, error, diff, clearDiff: () => setDiff(null),
    send, act, restart: () => start(true), setError,
  }
}
