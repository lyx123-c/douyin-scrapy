const { spawn } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..')
const devUrl = process.env.ELECTRON_DEV_URL || 'http://127.0.0.1:5173/'
const devAddress = new URL(devUrl)
const devPort = devAddress.port || (devAddress.protocol === 'https:' ? '443' : '80')
const viteBin = path.join(projectRoot, 'node_modules', 'vite', 'bin', 'vite.js')
const electronPath = require('electron')

let viteProcess = null
let electronProcess = null
let shuttingDown = false

function isReachable(url, timeoutMs = 800) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume()
      resolve(response.statusCode >= 200 && response.statusCode < 500)
    })
    request.setTimeout(timeoutMs, () => {
      request.destroy()
      resolve(false)
    })
    request.on('error', () => resolve(false))
  })
}

async function waitForUrl(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await isReachable(url)) {
      return
    }
    if (viteProcess && viteProcess.exitCode !== null) {
      throw new Error(`Vite 开发服务器提前退出（code=${viteProcess.exitCode}）。`)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`等待 Vite 开发服务器超时：${url}`)
}

function stopChildren() {
  if (shuttingDown) {
    return
  }
  shuttingDown = true
  if (electronProcess && electronProcess.exitCode === null) {
    electronProcess.kill()
  }
  if (viteProcess && viteProcess.exitCode === null) {
    viteProcess.kill()
  }
}

async function main() {
  if (!(await isReachable(devUrl))) {
    viteProcess = spawn(
      process.execPath,
      [viteBin, '--host', devAddress.hostname, '--port', devPort, '--strictPort'],
      {
        cwd: projectRoot,
        stdio: 'inherit',
        windowsHide: true,
      },
    )
    await waitForUrl(devUrl)
  }

  electronProcess = spawn(electronPath, ['.'], {
    cwd: projectRoot,
    stdio: 'inherit',
    windowsHide: false,
    env: {
      ...process.env,
      ELECTRON_DEV_URL: devUrl,
    },
  })

  electronProcess.on('exit', (code) => {
    stopChildren()
    process.exit(code ?? 0)
  })
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    stopChildren()
    process.exit(0)
  })
}

main().catch((error) => {
  console.error(`[electron:start] ${error.message}`)
  stopChildren()
  process.exit(1)
})
