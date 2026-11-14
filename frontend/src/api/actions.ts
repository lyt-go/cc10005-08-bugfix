/**
 * 动作调用统一封装：后端动作接口 HTTP 恒为 200，业务成败看 body.ok。
 * 旧实现只判断 response.ok，导致 ok:false 的准入/冲突失败被当成成功，
 * 页面随后用旧数据刷新，表现为“动作不生效、显示旧状态”。这里统一纠正。
 */
import { request } from '@/api/client'

export interface ActionOutcome {
  ok: boolean
  message: string
  entry?: Record<string, unknown> | null
}

export async function postAction(
  endpoint: string,
  id: string | number,
  body: Record<string, unknown>,
): Promise<ActionOutcome> {
  const response = await request(`${endpoint}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = `接口返回 ${response.status}`
    try {
      const data = await response.json()
      detail = data?.detail ?? detail
    } catch {
      /* 忽略非 JSON 错误体 */
    }
    return { ok: false, message: detail }
  }
  const data = (await response.json()) as ActionOutcome
  return { ok: data.ok, message: data.message, entry: data.entry ?? null }
}

/** 后端状态机：当前状态下允许的动作（与后端 CRAWL_ALLOWED / SITE_ALLOWED 对齐）。 */
export function allowedActions(
  status: string,
  table: Record<string, string[]>,
): string[] {
  return table[status] ?? []
}

export const CRAWL_ACTION_MAP: Record<string, string[]> = {
  草稿: ['提交策略评审'],
  策略评审中: ['启用爬取'],
  已启用: ['暂停爬取'],
  已暂停: ['提交策略评审'],
}

export const SITE_ACTION_MAP: Record<string, string[]> = {
  草稿: ['发起探测'],
  探测中: ['确认识别', '标记屏蔽'],
  已识别: ['标记屏蔽'],
  已屏蔽: ['发起探测'],
}
