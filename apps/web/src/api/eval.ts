import { apiPostJson } from './http'

export async function runEval(kind: string) {
  return apiPostJson('/eval/runs', { kind })
}
