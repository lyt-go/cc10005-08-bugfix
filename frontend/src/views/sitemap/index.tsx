import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { SITE_ACTION_MAP, allowedActions, postAction } from '@/api/actions'

const ENDPOINT = '/api/sitemap'
const columns = [
  '站点编号',
  '站点域名',
  'robots规则',
  '状态',
  '识别版本',
  '版本',
  '最近探测时间',
]
const ALL_ACTIONS = ['发起探测', '确认识别', '标记屏蔽']
const STATUS_OPTIONS = ['草稿', '探测中', '已识别', '已屏蔽']

type Row = Record<string, string | number | null>
type BlockEvent = { 探测时间: string; 屏蔽原因: string; 站点版本: number }
type DetailRow = Row & { 屏蔽事件?: BlockEvent[] }

export default function SitemapView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<{ keyword: string; status: string }>({ keyword: '', status: '' })
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<DetailRow | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [blockTarget, setBlockTarget] = useState<Row | null>(null)
  const [blockReason, setBlockReason] = useState('')

  const loadDetail = useCallback(async (id: number) => {
    try {
      const response = await request(`${ENDPOINT}/${id}`)
      if (!response.ok) {
        throw new Error('站点描述详情读取失败')
      }
      setDetail((await response.json()) as DetailRow)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '站点地图详情读取失败')
    }
  }, [])

  const reload = useCallback(async () => {
    // 后端只识别 keyword/status 两个查询参数，中文展示名不能直接作为 query key
    const params = new URLSearchParams()
    if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
    if (filters.status) params.set('status', filters.status)
    const query = params.toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('站点描述列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      if (detailId !== null) {
        void loadDetail(detailId)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '站点地图列表读取失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, detailId])

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

  async function runAction(action: string, row: Row, reason?: string) {
    setErrorMessage('')
    setInfoMessage('')
    setBusyId(Number(row.id))
    try {
      const outcome = await postAction(ENDPOINT, row.id as number, {
        action,
        version: row.version,
        ...(reason ? { reason } : {}),
      })
      if (!outcome.ok) {
        setErrorMessage(outcome.message)
        return
      }
      setInfoMessage(outcome.message)
      await reload()
    } finally {
      setBusyId(null)
    }
  }

  function confirmBlock() {
    if (!blockTarget) return
    const reason = blockReason.trim()
    if (!reason) {
      setErrorMessage('标记屏蔽必须填写屏蔽原因')
      return
    }
    const target = blockTarget
    setBlockTarget(null)
    setBlockReason('')
    void runAction('标记屏蔽', target, reason)
  }

  const stats = useMemo(() => ({
    total: rows.length,
    identified: rows.filter((r) => r.status === '已识别').length,
    probing: rows.filter((r) => r.status === '探测中').length,
    blocked: rows.filter((r) => r.status === '已屏蔽').length,
  }), [rows])

  return (
    <section className="page" data-module="sitemap">
      <header className="page-head">
        <div>
          <h2>站点地图管理</h2>
          <p className="page-desc">
            维护目标站点结构描述。状态沿 草稿 → 探测中 → 已识别 → 已屏蔽 单向推进；
            屏蔽需记录探测时间与原因，已屏蔽只能重新探测识别，产生新的站点识别版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('站点描述登记入口尚未接入审批流')}>
            登记站点描述
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出站点地图清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">站点描述总数</span><strong className="stat-value">{stats.total}</strong></article>
        <article className="stat-card"><span className="stat-label">已识别站点</span><strong className="stat-value">{stats.identified}</strong></article>
        <article className="stat-card"><span className="stat-label">探测中站点</span><strong className="stat-value">{stats.probing}</strong></article>
        <article className="stat-card"><span className="stat-label">已屏蔽站点</span><strong className="stat-value">{stats.blocked}</strong></article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>站点编号</span>
          <input
            value={filters.keyword}
            placeholder="按站点编号检索"
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
            const permitted = allowedActions(String(row.status ?? ''), SITE_ACTION_MAP)
            return (
              <Fragment key={String(row.id)}>
                <tr>
                  {columns.map((column) => (
                    <td key={column}>
                      {column === '版本'
                        ? `v${String(row.version ?? 1)}`
                        : column === '识别版本'
                          ? (Number(row.识别版本) > 0 ? `v${String(row.识别版本)}` : '未识别')
                          : (row[column] ?? '—')}
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
                          onClick={() => {
                            if (action === '标记屏蔽') {
                              setBlockTarget(row)
                              setBlockReason('')
                              setErrorMessage('')
                            } else {
                              void runAction(action, row)
                            }
                          }}
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
                      <SitemapDetail detail={detail} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无站点地图数据，可先登记站点描述</td>
            </tr>
          )}
        </tbody>
      </table>

      {blockTarget && (
        <div className="modal-mask" onClick={() => setBlockTarget(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h4>标记屏蔽 · {String(blockTarget.站点编号)}</h4>
            <p className="muted">屏蔽将记录探测时间与原因，并使关联爬虫的启用资格失效。</p>
            <label className="filter-item">
              <span>屏蔽原因（必填）</span>
              <textarea
                rows={3}
                value={blockReason}
                placeholder="例如：robots 协议变更，整站禁止采集"
                onChange={(e) => setBlockReason(e.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setBlockTarget(null)}>取消</button>
              <button className="btn primary" type="button" onClick={confirmBlock}>确认屏蔽</button>
            </div>
          </div>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条站点描述记录</span>
      </footer>
    </section>
  )
}

function SitemapDetail({ detail }: { detail: DetailRow }) {
  const events = detail.屏蔽事件 ?? []
  return (
    <div className="detail-panel">
      <h4>站点描述详情 · {String(detail.站点编号)}</h4>
      <dl className="detail-grid">
        <div><dt>站点域名</dt><dd>{String(detail.站点域名 ?? '—')}</dd></div>
        <div><dt>robots规则</dt><dd>{String(detail.robots规则 ?? '—')}</dd></div>
        <div><dt>sitemap地址</dt><dd>{String(detail.sitemap地址 ?? '—')}</dd></div>
        <div><dt>页面模板路径</dt><dd>{String(detail.页面模板路径 ?? '—')}</dd></div>
        <div><dt>翻页参数名</dt><dd>{String(detail.翻页参数名 ?? '—')}</dd></div>
        <div><dt>站点字符编码</dt><dd>{String(detail.站点字符编码 ?? '—')}</dd></div>
        <div><dt>识别版本</dt><dd>{Number(detail.识别版本) > 0 ? `v${String(detail.识别版本)}` : '未识别'}</dd></div>
        <div><dt>最近探测时间</dt><dd>{String(detail.最近探测时间 ?? '—')}</dd></div>
        <div><dt>当前屏蔽原因</dt><dd>{String(detail.屏蔽原因 || '—')}</dd></div>
      </dl>
      <div className="path-block">
        <h5>屏蔽事件记录（{events.length} 条，恢复后仍保留）</h5>
        {events.length ? (
          <table className="sub-table">
            <thead><tr><th>探测时间</th><th>屏蔽原因</th><th>当时站点版本</th></tr></thead>
            <tbody>
              {events.map((evt, idx) => (
                <tr key={idx}>
                  <td>{evt.探测时间}</td>
                  <td>{evt.屏蔽原因}</td>
                  <td>v{String(evt.站点版本)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">暂无屏蔽事件</p>}
      </div>
    </div>
  )
}
