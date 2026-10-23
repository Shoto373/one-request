import type {
  ApiErrorDetail, Cart, CartIn, CheckoutIn, CommandIn, MessageIn, Order, SessionCreated, SessionView, StreamEvent,
  TraceView,
} from './types'
import { ApiError } from './errors'
import { mockApi } from './mock'

export { ApiError }
export const IS_MOCK = import.meta.env.VITE_MOCK === '1'

async function toError(res: Response): Promise<ApiError> {
  let detail: ApiErrorDetail = { code: 'http_' + res.status, message: res.statusText || 'Ошибка запроса' }
  try {
    const body = await res.json()
    if (body?.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) detail = body.detail
    else if (typeof body?.detail === 'string') detail = { code: detail.code, message: body.detail }
    else if (Array.isArray(body?.detail)) detail = { code: 'validation', message: 'Проверьте введённые данные' }
  } catch {
    /* не JSON */
  }
  return new ApiError(res.status, detail)
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!res.ok) throw await toError(res)
  return res.json() as Promise<T>
}

/** POST, ответ — поток Server-Sent Events. EventSource не умеет POST, поэтому читаем руками. */
async function stream(path: string, body: unknown, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw await toError(res)

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value.replace(/\r\n/g, '\n')
    let sep: number
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) continue
      try {
        onEvent({ event, data: JSON.parse(data.join('\n')) } as StreamEvent)
      } catch {
        console.warn('[sse] не JSON:', data.join('\n'))
      }
    }
  }
}

const realApi = {
  createSession: () => json<SessionCreated>('/api/sessions', { method: 'POST', body: '{}' }),
  getSession: (id: string) => json<SessionView>(`/api/sessions/${id}`),
  getTrace: (id: string) => json<TraceView>(`/api/sessions/${id}/trace`),
  sendMessage: (id: string, body: MessageIn, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) =>
    stream(`/api/sessions/${id}/messages`, body, onEvent, signal),
  sendCommand: (id: string, body: CommandIn, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) =>
    stream(`/api/sessions/${id}/commands`, body, onEvent, signal),
  createCart: (body: CartIn) => json<Cart>('/api/cart', { method: 'POST', body: JSON.stringify(body) }),
  getCart: (id: string) => json<Cart>(`/api/cart/${id}`),
  checkout: (cartId: string, body: CheckoutIn) =>
    json<Order>(`/api/cart/${cartId}/checkout`, { method: 'POST', body: JSON.stringify(body) }),
  getOrder: (id: string) => json<Order>(`/api/orders/${id}`),
}

export type Api = typeof realApi

export const api: Api = IS_MOCK ? mockApi : realApi
