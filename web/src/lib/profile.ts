/** Анонимный профиль браузера для памяти предпочтений (app/profile.py). Не логин — просто id в localStorage. */
const KEY = 'trip_profile_id'

export function profileId(): string | undefined {
  try {
    let id = localStorage.getItem(KEY)
    if (!id) {
      id = crypto.randomUUID()
      localStorage.setItem(KEY, id)
    }
    return id
  } catch {
    return undefined // приватный режим: без памяти, всё остальное работает
  }
}
