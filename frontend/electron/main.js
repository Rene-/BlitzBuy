/**
 * BlitzBuy — Electron main process
 *
 * Responsibilities:
 *  1. Create the BrowserWindow and load the React build
 *  2. Expose IPC handlers so the renderer can invoke blitzbuy.exe / blitzbuy.py
 *  3. Stream stdout/stderr back to the renderer in real time
 *  4. Write every session to a persistent daily log file in userData/logs/
 */

const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path  = require('path')
const { spawn } = require('child_process')
const fs    = require('fs')

// ── Session logger ────────────────────────────────────────────────────────────
// Logs are written to:  %APPDATA%\BlitzBuy\logs\YYYY-MM-DD.log
// One file per day — easy to find, easy to share for debugging.

function logsDir() {
  const dir = path.join(app.getPath('userData'), 'logs')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

function todayLogPath() {
  const date = new Date().toISOString().slice(0, 10)   // YYYY-MM-DD
  return path.join(logsDir(), `${date}.log`)
}

function writeLog(line) {
  const ts    = new Date().toISOString()
  const entry = `[${ts}] ${line}\n`
  try {
    fs.appendFileSync(todayLogPath(), entry, 'utf8')
  } catch (e) {
    // Never crash the app over a log write failure
  }
}

// ── Resolve the Python runner ─────────────────────────────────────────────────
// In production (packaged) we ship blitzbuy.exe next to the app resources.
// In development we fall back to running blitzbuy.py via python directly.

function resolvePythonRunner() {
  if (app.isPackaged) {
    const exePath = path.join(process.resourcesPath, 'blitzbuy.exe')
    if (fs.existsSync(exePath)) return { cmd: exePath, args: [] }
  }
  const scriptPath = path.join(__dirname, '..', '..', 'blitzbuy.py')
  return { cmd: 'python', args: [scriptPath] }
}

// ── Window ────────────────────────────────────────────────────────────────────

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
  })

  const indexHtml = path.join(__dirname, '..', 'dist', 'index.html')
  mainWindow.loadFile(indexHtml)

  writeLog(`=== BlitzBuy session started (v${app.getVersion()}) ===`)

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
  writeLog('=== BlitzBuy session ended ===')
  if (process.platform !== 'darwin') app.quit()
})

// ── IPC: run-purchase ─────────────────────────────────────────────────────────

ipcMain.handle('run-purchase', async (event, jobConfig) => {
  return new Promise((resolve) => {
    const { cmd, args } = resolvePythonRunner()

    writeLog(`JOB START  url=${jobConfig.url}  product="${jobConfig.product}"  maxPrice=${jobConfig.maxPrice}`)

    const env = {
      ...process.env,
      BLITZBUY_URL:      jobConfig.url,
      BLITZBUY_USER:     jobConfig.email,
      BLITZBUY_PASS:     jobConfig.password,
      BLITZBUY_EMAIL:    jobConfig.email,
      BLITZBUY_PRODUCT:  jobConfig.product,
      BLITZBUY_MAX_PRICE: String(jobConfig.maxPrice),
      PYTHONUNBUFFERED:  '1',
    }

    const child = spawn(cmd, args, { env, shell: false })
    const logs  = []

    function sendLog(line) {
      logs.push(line)
      writeLog(`  ${line}`)
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
      writeLog(`JOB END    status=${success ? 'SUCCESS' : 'FAILED'}  exitCode=${code}`)
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
      writeLog(`JOB ERROR  ${err.message}`)
      resolve({ success: false, message: err.message, logs })
    })
  })
})

// ── IPC: open-logs-folder ─────────────────────────────────────────────────────

ipcMain.handle('open-logs-folder', () => {
  shell.openPath(logsDir())
})

// ── IPC: get-log-path ─────────────────────────────────────────────────────────

ipcMain.handle('get-log-path', () => todayLogPath())
