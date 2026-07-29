import { apiGet } from './http'

export async function fetchUsage() {
  return apiGet('/metrics/usage')
}
