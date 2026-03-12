import { useState, useCallback, useEffect } from 'react'
import PurchaseForm    from './components/PurchaseForm'
import PurchaseTracker from './components/PurchaseTracker'
import './App.css'

// ─────────────────────────────────────────────────────────────────────────────
// Runtime detection
//   window.blitzBuyAPI  → injected by electron/preload.js → use real runner
//   otherwise           → browser dev mode → use simulation
// ─────────────────────────────────────────────────────────────────────────────
const IS_ELECTRON = typeof window !== 'undefined' && !!window.blitzBuyAPI

function delay(ms) { return new Promise(r => setTimeout(r, ms)) }

/** Browser simulation — mimics the real blitzbuy.py result shape */
async function simulatePurchase(job, onUpdate) {
  await delay(300)
  onUpdate(job.id, { status: 'running', startedAt: new Date().toISOString() })

  const duration = 2000 + Math.random() * 3000
  await delay(duration)

  const ok = Math.random() > 0.2
  onUpdate(job.id, {
    status:    ok ? 'success' : 'failed',
    elapsedMs: Math.round(duration),
    message: ok
      ? `Purchase of "${job.product}" completed successfully.`
      : `Timeout: no product matching "${job.product}" found under $${job.maxPrice.toFixed(2)}.`,
    screenshot: ok ? `screenshots/success_order_complete_${Date.now()}.png` : null,
    logs: ['[simulated — run in Electron for real automation]'],
  })
}

/** Electron real runner — invokes blitzbuy.exe via IPC */
async function runElectronPurchase(job, onUpdate) {
  onUpdate(job.id, { status: 'running', startedAt: new Date().toISOString() })
  const t0     = performance.now()
  const result = await window.blitzBuyAPI.runPurchase(job)
  const elapsed = Math.round(performance.now() - t0)

  onUpdate(job.id, {
    status:    result.success ? 'success' : 'failed',
    elapsedMs: elapsed,
    message:   result.message,
    logs:      result.logs ?? [],
    screenshot: null,   // path is printed in logs by blitzbuy.py
  })
}

// ─────────────────────────────────────────────────────────────────────────────

let _id = 1
const uid = () => `job-${_id++}-${Date.now()}`

export default function App() {
  const [jobs,      setJobs]      = useState([])
  const [isRunning, setIsRunning] = useState(false)

  // Subscribe to real-time log lines from Electron main process
  useEffect(() => {
    if (!IS_ELECTRON) return
    const unsub = window.blitzBuyAPI.onLog(({ jobId, line }) => {
      setJobs(prev => prev.map(j =>
        j.id === jobId
          ? { ...j, logs: [...(j.logs ?? []), line] }
          : j
      ))
    })
    return unsub
  }, [])

  const updateJob = useCallback((id, patch) => {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, ...patch } : j))
  }, [])

  async function handleSubmit(formData) {
    const job = {
      id:         uid(),
      status:     'pending',
      url:        formData.url,
      product:    formData.product,
      maxPrice:   formData.maxPrice,
      email:      formData.email,
      password:   formData.password,
      startedAt:  null,
      elapsedMs:  null,
      message:    null,
      screenshot: null,
      logs:       [],
    }

    setJobs(prev => [...prev, job])
    setIsRunning(true)

    try {
      if (IS_ELECTRON) {
        await runElectronPurchase(job, updateJob)
      } else {
        await simulatePurchase(job, updateJob)
      }
    } finally {
      setIsRunning(false)
    }
  }

  return (
    <div className="app">
      {/* Full-width drag strip fixed at the very top of the window.
          Sits outside the centered container so it spans edge-to-edge.
          The app-header content sits below it and is purely visual. */}
      {IS_ELECTRON && <div className="drag-strip" />}
      <header className="app-header">
        <h1>Blitz<span>Buy</span></h1>
        <span className="badge">{IS_ELECTRON ? 'ELECTRON' : 'DEMO'}</span>
      </header>

      <PurchaseForm onSubmit={handleSubmit} isRunning={isRunning} />
      <PurchaseTracker
        jobs={jobs}
        onRemove={id => setJobs(prev => prev.filter(j => j.id !== id))}
        onClearAll={() => setJobs([])}
      />
    </div>
  )
}
