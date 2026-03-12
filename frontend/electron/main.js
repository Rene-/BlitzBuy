/**
 * BlitzBuy — Electron main process
 *
 * Responsibilities:
 *  1. Create the BrowserWindow and load the React build
 *  2. Expose IPC handlers so the renderer can invoke blitzbuy.exe / blitzbuy.py
 *  3. Stream stdout/stderr back to the renderer in real time
 */

const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path  = require('path')
const { spawn } = require('child_process')
const fs    = require('fs')

// ── Resolve the Python runner ────────────────────────────────────────────────
// In production (packaged) we ship blitzbuy.exe next to the app resources.
// In development we fall back to running blitzbuy.py via python directly.

function resolvePythonRunner() {
  if (app.isPackaged) {
    // electron-builder places extraResources into process.resourcesPath
    const exePath = path.join(process.resourcesPath, 'blitzbuy.exe')
    if (fs.existsSync(exePath)) return { cmd: exePath, args: [] }
  }
  // Dev fallback — run the .py script
  const scriptPath = path.join(__dirname, '..', '..', 'blitzbuy.py')
  return { cmd: 'python', args: [scriptPath] }
}

// ── Window ───────────────────────────────────────────────────────────────────

let mainWindow

function createWindow() {
  mainWindow = new BrowserWindow({
    width:  940,
    height: 780,
    minWidth: 600,
    minHeight: 500,
    backgroundColor: '#0f1117',
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color:       '#1a1d27',
      symbolColor: '#e2e8f0',
      height: 32,
    },
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
    // icon: set a .ico file here for a custom app icon
  })

  const indexHtml = path.join(__dirname, '..', 'dist', 'index.html')
  mainWindow.loadFile(indexHtml)

  // Open external links in the system browser, not Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
}

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// ── IPC: run-purchase ────────────────────────────────────────────────────────
//
// The renderer sends:  { url, product, maxPrice, email, password }
// We spawn blitzbuy.exe (or blitzbuy.py), passing args as env vars so
// credentials never appear in process lists.
// We stream stdout/stderr back line-by-line via 'purchase-log' events,
// then send a final 'purchase-done' with the result.

ipcMain.handle('run-purchase', async (event, jobConfig) => {
  return new Promise((resolve) => {
    const { cmd, args } = resolvePythonRunner()

    const env = {
      ...process.env,
      BLITZBUY_URL:      jobConfig.url,
      BLITZBUY_USER:     jobConfig.email,
      BLITZBUY_PASS:     jobConfig.password,
      BLITZBUY_EMAIL:    jobConfig.email,
      PYTHONUNBUFFERED:  '1',   // ensure stdout isn't buffered
    }

    // Inject PRODUCT / MAX_PRICE as env vars too (blitzbuy.py reads them)
    env.BLITZBUY_PRODUCT   = jobConfig.product
    env.BLITZBUY_MAX_PRICE = String(jobConfig.maxPrice)

    const child = spawn(cmd, args, { env, shell: false })
    const logs  = []

    function sendLog(line) {
      logs.push(line)
      // Forward to renderer if window is still open
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('purchase-log', { jobId: jobConfig.id, line })
      }
    }

    child.stdout.on('data', d =>
      d.toString().split('\n').filter(Boolean).forEach(sendLog)
    )
    child.stderr.on('data', d =>
      d.toString().split('\n').filter(Boolean).forEach(sendLog)
    )

    child.on('close', code => {
      const success = code === 0
      resolve({
        success,
        message: success
          ? `Purchase of "${jobConfig.product}" completed.`
          : `Process exited with code ${code}.`,
        logs,
      })
    })

    child.on('error', err => {
      sendLog(`[ERROR] Failed to start runner: ${err.message}`)
      resolve({ success: false, message: err.message, logs })
    })
  })
})
