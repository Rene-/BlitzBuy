/**
 * Preload — IPC bridge between Electron main and the React renderer.
 *
 * Only explicitly-allowlisted APIs are exposed via contextBridge,
 * keeping the renderer in a sandboxed, least-privilege context.
 */

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('blitzBuyAPI', {
  /**
   * Run a purchase job.
   * @param {object} jobConfig  { id, url, product, maxPrice, email, password }
   * @returns {Promise<{success:boolean, message:string, logs:string[]}>}
   */
  runPurchase: (jobConfig) => ipcRenderer.invoke('run-purchase', jobConfig),

  /**
   * Subscribe to real-time log lines from the Python runner.
   * @param {function} cb  (payload: {jobId:string, line:string}) => void
   * @returns {function}  call returned fn to unsubscribe
   */
  onLog: (cb) => {
    const handler = (_event, payload) => cb(payload)
    ipcRenderer.on('purchase-log', handler)
    return () => ipcRenderer.off('purchase-log', handler)
  },
})
