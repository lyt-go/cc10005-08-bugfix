import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { CRAWL_ACTION_MAP, allowedActions, postAction } from '@/api/actions'

const ENDPOINT = '/api/deepcrawl'
const columns = [
  '配置编号',
  '绑定站点编号',
  '种子URL',
  '最大深度',
  '路径排除规则',
  '状态',
  '版本',
]
const ALL_ACTIONS = ['提交策略评审', '启用爬取', '暂停爬取']
const STATUS_OPTIONS = ['草稿', '策略评审中', '已启用', '已暂停']

type Row = Record<string, string | number | null>
type Admission = {
  绑定站点编号?: string
  站点存在?: boolean
  站点状态?: string | null
  站点识别版本?: number | null
  路径排除规则有效?: boolean
  可启用?: boolean
  可评审?: boolean
  阻断原因?: string
  评审阻断原因?: string
}
type DetailRow = Row & { 准入状态?: Admission }

export default function DeepcrawlView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<{ keyword: string; status: string }>({ keyword: '', status: '' })
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<DetailRow | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const reload = useCallback(async () => {
    // 后端只识别 keyword/status 两个查询参数，中文展示名不能直接作为 query key
    const params = new URLSearchParams()
    if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
    if (filters.status) params.set('status', filters.status)
    const query = params.toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('爬虫配置列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      // 详情面板打开时同步刷新，保证列表与详情持久一致
      if (detailId !== null) {
        void loadDetail(detailId)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '深度爬取列表读取失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, detailId])

  const loadDetail = useCallback(async (id: number) => {
    try {
      const response = await request(`${ENDPOINT}/${id}`)
      if (!response.ok) {
        throw new Error('爬虫配置详情读取失败')
      }
      setDetail((await response.json()) as DetailRow)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '深度爬取详情读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function toggleDetail(row: Row) {
    const id = Number(row.id)
    if (detailId === id) {
      setDetailId(null)
      setDetail(null)
      return
    }
    setDetailId(id)
    await loadDetail(id)
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    setBusyId(Number(row.id))
    try {
      // 携带当前版本做乐观并发控制；后端发现版本漂移会回传冲突版本
      const outcome = await postAction(ENDPOINT, row.id as number, {
        action,
        version: row.version,
      })
      if (!outcome.ok) {
        // 关键修复：业务失败（ok:false）必须显式提示，不能静默刷新成旧状态
        setErrorMessage(outcome.message)
        return
      }
      setInfoMessage(outcome.message)
      await reload()
    } finally {
      setBusyId(null)
    }
  }

  const stats = useMemo(() => ({
    total: rows.length,
    enabled: rows.filter((r) => r.status === '已启用').length,
    reviewing: rows.filter((r) => r.status === '策略评审中').length,
    paused: rows.filter((r) => r.status === '已暂停').length,
  }), [rows])

  return (
    <section className="page" data-module="deepcrawl">
      <header className="page-head">
        <div>
          <h2>深度爬取管理</h2>
          <p className="page-desc">
            管理网站深度爬取配置。状态只能沿 草稿 → 策略评审中 → 已启用 → 已暂停 单向推进；
            启用必须引用当前有效的站点识别版本与路径排除规则，暂停后旧路径冻结，重新评审须基于新站点版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('爬虫配置登记入口尚未接入审批流')}>
            登记爬虫配置
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出深度爬取清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">爬虫配置总数</span><strong className="stat-value">{stats.total}</strong></article>
        <article className="stat-card"><span className="stat-label">已启用配置</span><strong className="stat-value">{stats.enabled}</strong></article>
        <article className="stat-card"><span className="stat-label">评审中配置</span><strong className="stat-value">{stats.reviewing}</strong></article>
        <article className="stat-card"><span className="stat-label">已暂停配置</span><strong className="stat-value">{stats.paused}</strong></article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>配置编号</span>
          <input
            value={filters.keyword}
            placeholder="按配置编号检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, keyword: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select
            value={filters.status}
            onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}
          >
            <option value="">全部状态</option>
            {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({ keyword: '', status: '' })}>重置条件</button>
      </form>

      {errorMessage && <div className="banner error">{errorMessage}</div>}
      {infoMessage && <div className="banner info">{infoMessage}</div>}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const permitted = allowedActions(String(row.status ?? ''), CRAWL_ACTION_MAP)
            return (
              <Fragment key={String(row.id)}>
                <tr>
                  {columns.map((column) => (
                    <td key={column}>
                      {column === '版本' ? `v${String(row.version ?? 1)}` : (row[column] ?? '—')}
                    </td>
                  ))}
                  <td className="row-actions">
                    {ALL_ACTIONS.map((action) => {
                      const allowed = permitted.includes(action)
                      return (
                        <button
                          key={action}
                          className="link"
                          type="button"
                          disabled={!allowed || busyId === Number(row.id)}
                          title={allowed ? action : `当前「${row.status}」状态不可执行「${action}」`}
                          onClick={() => void runAction(action, row)}
                        >
                          {action}
                        </button>
                      )
                    })}
                  </td>
                  <td>
                    <button className="link" type="button" onClick={() => void toggleDetail(row)}>
                      {detailId === Number(row.id) ? '收起' : '查看详情'}
                    </button>
                  </td>
                </tr>
                {detailId === Number(row.id) && detail && (
                  <tr className="detail-row">
                    <td colSpan={columns.length + 2}>
                      <DeepcrawlDetail detail={detail} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无深度爬取数据，可先登记爬虫配置</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条深度爬取记录</span>
      </footer>
    </section>
  )
}

function DeepcrawlDetail({ detail }: { detail: DetailRow }) {
  const admission = detail.准入状态
  const done = (detail.已完成路径 as unknown as string[] | undefined) ?? []
  const history = (detail.历史路径代次 as unknown as Array<Record<string, unknown>> | undefined) ?? []
  return (
    <div className="detail-panel">
      <h4>爬虫配置详情 · {String(detail.配置编号)}</h4>
      <dl className="detail-grid">
        <div><dt>种子URL</dt><dd>{String(detail.种子URL ?? '—')}</dd></div>
        <div><dt>最大深度</dt><dd>{String(detail.最大深度 ?? '—')}</dd></div>
        <div><dt>同域约束</dt><dd>{String(detail.同域约束 ?? '—')}</dd></div>
        <div><dt>路径包含规则</dt><dd>{String(detail.路径包含规则 ?? '—')}</dd></div>
        <div><dt>路径排除规则</dt><dd>{String(detail.路径排除规则 ?? '—')}</dd></div>
        <div><dt>并发线程数</dt><dd>{String(detail.并发线程数 ?? '—')}</dd></div>
        <div><dt>断点续爬</dt><dd>{String(detail.断点续爬 ?? '—')}</dd></div>
        <div><dt>评审代次</dt><dd>第 {String(detail.评审代次 ?? 0)} 代</dd></div>
        <div><dt>评审引用站点版本</dt><dd>{detail.评审站点版本 == null ? '未锁定' : `v${String(detail.评审站点版本)}`}</dd></div>
        <div><dt>暂停时间</dt><dd>{String(detail.暂停时间 || '—')}</dd></div>
      </dl>

      {admission && (
        <div className={`admission ${admission.可启用 ? 'ok' : 'blocked'}`}>
          <strong>准入判定：</strong>
          站点 {String(admission.绑定站点编号 || '—')}
          {admission.站点存在 ? `（${String(admission.站点状态)}，识别版本 v${String(admission.站点识别版本 ?? 0)}）` : '（不存在）'}
          ，可评审：{admission.可评审 ? '是' : '否'}，可启用：{admission.可启用 ? '是' : '否'}
          {admission.阻断原因 && <div className="block-reason">启用阻断：{admission.阻断原因}</div>}
          {admission.评审阻断原因 && <div className="block-reason">评审阻断：{admission.评审阻断原因}</div>}
        </div>
      )}

      <div className="path-block">
        <h5>当前代次已完成路径（暂停后保留，{done.length} 条）</h5>
        {done.length ? <ul>{done.map((p) => <li key={p}>{p}</li>)}</ul> : <p className="muted">暂无</p>}
        <h5>历史代次冻结路径（重启策略不会激活）</h5>
        {history.length ? (
          history.map((gen, idx) => (
            <div key={idx} className="history-gen">
              第 {String(gen.评审代次)} 代 · 冻结于 {String(gen.冻结时间)} · 原站点版本 v{String(gen.原站点识别版本 ?? 0)}
              <ul>{((gen.已完成路径 as string[]) ?? []).map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          ))
        ) : <p className="muted">暂无</p>}
      </div>
    </div>
  )
}
