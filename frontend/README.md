# 抖音视频工作台前端与桌面壳

本目录包含 Vue 3 前端、Electron 主进程、Preload Bridge，以及 FastAPI Backend 的 Windows 打包入口。

## 运行模式

- `npm run electron:start`：开发模式，自动复用或启动 Vite，并使用项目可用的 Python 环境启动 FastAPI。
- `npm run electron:prod`：本地未打包运行，执行前端构建后由 Electron 加载 `dist/index.html`，Backend 仍使用开发 Python。
- `npm run backend:build`：使用 PyInstaller 生成独立 Backend 目录。
- `npm run ffmpeg:verify`：校验随包 FFmpeg 的版本、哈希、许可证和所需 codec。
- `npm run electron:build`：构建 Backend、前端和完整的 `release/win-unpacked` 应用目录。
- `npm run electron:package`：构建 Windows NSIS Installer。
- `npm run release:manifest`：生成发行清单和第三方许可证清单。
- `npm run release:verify`：验证 Installer、NSIS 策略和生产资源。
- `npm run release:package`：执行完整发布构建、清单生成和发行验证。
- `npm run storage:test`：验证存储配置、目录约束、复制与 SHA256 校验。
- `npm run version:metadata`：生成 Electron、Backend 与发布系统共用的版本元数据。
- `npm run update:test`：执行更新 Manifest、签名、防重放和路径安全测试。

发布、签名、许可证、安装和卸载治理详见 [RELEASE.md](RELEASE.md)。
更新协议、Ed25519 信任模型和公共 Release 配置详见 [UPDATE-PROTOCOL.md](UPDATE-PROTOCOL.md)。
U1.2 的完整 NSIS 更新流程、进程关闭握手、下载校验和安装交接说明也记录在该协议文档中。

发布目录不依赖 Vite Dev Server、IDE、系统 Python 或项目虚拟环境。Electron 通过 `file://` 加载 `app.asar/dist/index.html`，并直接启动 `resources/backend/Backend.exe`。

生产环境 FastAPI 地址由 Electron Main 根据实际 `DOUYIN_BACKEND_PORT` 生成，并通过 Preload Bridge 注入前端；`.env.production` 不保存固定 Backend 端口。Vite 开发代理仍使用开发环境配置。

## Backend 打包

```powershell
cd frontend
npm run backend:build
```

默认构建产物：

```text
frontend/
├── backend-dist/
│   └── Backend/
│       ├── Backend.exe
│       ├── runtime-manifest.json
│       ├── _internal/                 # Python Runtime 与依赖
│       └── playwright-browsers/
│           └── chromium-1248/         # 随包 Chromium
├── backend-build/                     # PyInstaller 工作目录
└── release/
    └── win-unpacked/
        ├── 抖音视频工作台.exe
        └── resources/
            ├── app.asar
            ├── ffmpeg/
            │   ├── ffmpeg.exe
            │   ├── LICENSE.txt
            │   ├── BUILD-INFO.json
            │   ├── VERSION.txt
            │   └── SOURCE-INFO.txt
            └── backend/
```

构建脚本优先使用 `DOUYIN_PYTHON_PATH`，其次检查项目 `.venv` 和当前开发机的 Python 3.13 环境。构建机需安装 `requirements-build.txt` 中的 PyInstaller，并已执行 Patchright Chromium 安装。生成的发布目录运行时不再依赖构建机 Python。

## FFmpeg 资源

随包二进制为 GPLv3 的 FFmpeg `N-128889-g88c46fce27-20261113`，固定 SHA256 为 `3D65AB9CCA95F0B1BBA23C03FF52711675B132A1456DD6C75BA379EE89E5A6ED`。只部署 `ffmpeg.exe`，不需要也未包含 `ffprobe.exe` 或 `ffplay.exe`。

- 生产模式由 Electron 固定传递 `resources/ffmpeg/ffmpeg.exe`。
- 开发模式继续使用 `FFMPEG_PATH`，默认保留当前本地开发配置。
- FFmpeg 资源位于 `vendor/ffmpeg`，不进入 `app.asar`。
- 许可证、对应源码信息和构建元数据随二进制一起部署。
- 构建前必须通过 `npm run ffmpeg:verify`。

## 用户数据边界

- 正式版首次启动必须由用户分别选择应用数据根目录和视频下载目录。
- `app.getPath('userData')/storage-settings.json` 只保存两个根目录路径，作为启动 Bootstrap；业务数据不默认写入 `%APPDATA%`。
- 应用数据根目录下使用固定子目录保存登录 Profile、视频数据库、任务队列、CSV 和日志。
- 视频下载目录与应用数据根目录相互独立，可以位于不同磁盘。
- 应用数据迁移由 Electron Main 执行：停止 Backend、复制、逐文件 SHA256 校验、切换配置、重启并核对 FastAPI 实际路径；失败时恢复旧配置。
- 视频目录修改只影响之后建立的下载任务；已入队任务保存入队时的目标目录。
- Cookie、浏览器 Profile、数据库、CSV、下载视频、日志和临时文件不得写入安装目录、`process.resourcesPath` 或 `app.asar`。
- 开发模式继续使用现有本地数据路径，不读取也不写入正式版存储配置。
- 测试可使用 `DOUYIN_STORAGE_CONFIG_PATH`、`DOUYIN_APP_USER_DATA_DIR`、`DOUYIN_SAVE_DIR` 和独立 `DOUYIN_BACKEND_PORT` 隔离运行。

## 开发启动

```powershell
C:\Users\ASUS\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。开发模式下 Axios 请求 `/backend/api/*`，由 Vite 代理到 `http://127.0.0.1:8000/api/*`。

## Smoke 检查

```powershell
frontend\backend-dist\Backend\Backend.exe --browser-smoke
```

成功时输出：

```text
BROWSER_SMOKE_TITLE=Backend Browser OK
```

Electron 生产 Smoke 必须使用临时目录和非默认端口，避免读写真实用户数据：

```powershell
$env:ELECTRON_SMOKE_TEST='1'
$env:DOUYIN_STORAGE_ALLOW_ENV='1'
$env:DOUYIN_STORAGE_CONFIG_PATH='C:\temp\douyin-smoke\storage-settings.json'
$env:DOUYIN_APP_USER_DATA_DIR='C:\temp\douyin-smoke\data'
$env:DOUYIN_SAVE_DIR='D:\temp\douyin-smoke\videos'
$env:DOUYIN_BACKEND_PORT='18080'
```
