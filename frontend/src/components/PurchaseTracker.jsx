import { useState } from 'react'

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusLabel(status) {
  return { pending: 'Pending', running: 'Running', success: 'Success', failed: 'Failed' }[status] ?? status
}

function fmtElapsed(ms) {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// ── Row ───────────────────────────────────────────────────────────────────────

function PurchaseRow({ job, onRemove }) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <div
        className="purchase-row"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(o => !o)}
      >
        {/* Status pill */}
        <span className={`status-pill ${job.status}`}>
          {job.status === 'running' && <span style={{ marginRight: '.35rem' }}>&#9679;</span>}
          {statusLabel(job.status)}
        </span>

        {/* Info */}
        <div className="purchase-info">
          <div className="purchase-url" title={job.url}>{job.url}</div>
          <div className="purchase-meta">
            {job.product} &nbsp;·&nbsp; max ${job.maxPrice.toFixed(2)}
            &nbsp;·&nbsp; {fmtTime(job.startedAt)}
          </div>
          {job.status === 'running' && (
            <div className="progress-bar-wrap">
              <div className="progress-bar-fill" style={{ width: '100%' }} />
            </div>
          )}
        </div>

        {/* Elapsed */}
        <div className="purchase-elapsed">
          {fmtElapsed(job.elapsedMs)}
        </div>

        {/* Remove */}
        <button
          className="btn-icon"
          title="Remove"
          onClick={e => { e.stopPropagation(); onRemove(job.id) }}
        >
          ✕
        </button>
      </div>

      {/* Expandable detail */}
      {open && (
        <div className="detail-panel">
          <strong>ID:</strong> {job.id}<br />
          <strong>URL:</strong> {job.url}<br />
          <strong>Product:</strong> {job.product}<br />
          <strong>Max price:</strong> ${job.maxPrice.toFixed(2)}<br />
          <strong>Status:</strong> {statusLabel(job.status)}<br />
          {job.elapsedMs != null && <><strong>Elapsed:</strong> {fmtElapsed(job.elapsedMs)}<br /></>}
          {job.message && (
            <>
              <strong>Message:</strong>
              <pre>{job.message}</pre>
            </>
          )}
          {job.screenshot && (
            <>
              <strong>Screenshot:</strong>
              <pre>{job.screenshot}</pre>
            </>
          )}
        </div>
      )}
    </>
  )
}

// ── Stats bar ─────────────────────────────────────────────────────────────────

function Stats({ jobs }) {
  const total   = jobs.length
  const running = jobs.filter(j => j.status === 'running').length
  const success = jobs.filter(j => j.status === 'success').length
  const failed  = jobs.filter(j => j.status === 'failed').length

  return (
    <div className="stats-row">
      <div className="stat-card">
        <span className="stat-label">Total</span>
        <span className="stat-value default">{total}</span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Running</span>
        <span className="stat-value pending">{running}</span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Success</span>
        <span className="stat-value success">{success}</span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Failed</span>
        <span className="stat-value error">{failed}</span>
      </div>
    </div>
  )
}

// ── Main tracker ──────────────────────────────────────────────────────────────

export default function PurchaseTracker({ jobs, onRemove, onClearAll }) {
  const [filter, setFilter] = useState('all')

  const filtered = filter === 'all' ? jobs : jobs.filter(j => j.status === filter)

  return (
    <>
      <Stats jobs={jobs} />

      <div className="card">
        <div className="tracker-header">
          <p className="card-title" style={{ margin: 0 }}>Purchase History</p>

          <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
            {/* Filter tabs */}
            {['all', 'running', 'success', 'failed'].map(f => (
              <button
                key={f}
                className={`btn btn-ghost`}
                style={{
                  fontSize: '.72rem',
                  padding: '.3rem .65rem',
                  ...(filter === f ? { color: 'var(--text)', borderColor: 'var(--accent)' } : {}),
                }}
                onClick={() => setFilter(f)}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}

            {jobs.length > 0 && (
              <button className="btn btn-danger" style={{ fontSize: '.72rem', padding: '.3rem .65rem' }} onClick={onClearAll}>
                Clear all
              </button>
            )}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">&#128722;</div>
            {jobs.length === 0
              ? 'No purchases yet — fill the form above and hit Run.'
              : `No ${filter} jobs.`}
          </div>
        ) : (
          <div className="purchase-list">
            {[...filtered].reverse().map(job => (
              <PurchaseRow key={job.id} job={job} onRemove={onRemove} />
            ))}
          </div>
        )}
      </div>
    </>
  )
}
