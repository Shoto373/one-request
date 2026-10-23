import type { ApiErrorDetail } from './types'

export class ApiError extends Error {
  constructor(public status: number, public detail: ApiErrorDetail) {
    super(detail.message)
  }
}
