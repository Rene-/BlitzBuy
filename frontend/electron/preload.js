/**
 * Preload — IPC bridge between Electron main and the React renderer.
 */

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('blitzBuyAPI', {
  /** Run a purchase job. */
  runPurchase: (jobConfig) => ipcRenderer.invoke('run-purchase', jobConfig),

  /** Subscribe to real-time log lines from the Python runner. */
  onLog: (cb) => {
    const handler = (_event, payload) => cb(payload)
    ipcRenderer.on('purchase-log', handler)
    return () => ipcRenderer.off('purchase-log', handler)
  },

  /** Open the logs folder in Windows Explorer. */
  openLogsFolder: () => ipcRenderer.invoke('open-logs-folder'),

  /** Get the path of today's log file. */
  getLogPath: () => ipcRenderer.invoke('get-log-path'),
})
