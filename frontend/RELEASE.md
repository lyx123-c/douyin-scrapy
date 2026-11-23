# Windows Release Governance

## Release Version

- Application version: `frontend/package.json` `version`
- Product name: `frontend/package.json` `build.productName`
- Electron Builder: pinned in `devDependencies`
- Installer artifact: `release/${productName}-${version}-x64.exe`

Do not change app version, appId, product name or NSIS upgrade identity without a migration review.

## Build

Full reproducible release command:

```powershell
cd frontend
npm run release:package
```

This performs:

1. FFmpeg hash/version/license/codec verification.
2. PyInstaller Backend build.
3. Vue production build.
4. Electron NSIS installer build.
5. Release manifest and third-party notices generation.
6. NSIS configuration and release resource verification.

Expected output:

```text
release/
├── 抖音视频工作台-0.2.0-x64.exe
├── RELEASE-MANIFEST.json
├── THIRD-PARTY-NOTICES.txt
└── win-unpacked/
```

## Installer Policy

Production Backend API routing:

- Electron Main owns the Backend URL and health check.
- The actual URL is injected through the preload bridge as `desktopRuntime.apiBaseUrl`.
- `.env.production` must not contain a fixed Backend port.
- Release smoke should run at least once with a non-default `DOUYIN_BACKEND_PORT`.
- Download UI must show disconnected/stale state when API requests fail.

- One-click installer disabled.
- Per-user installation.
- Installation directory can be changed.
- Electron, Backend and FFmpeg install together; users do not choose separate runtime directories.
- First launch requires valid, writable application-data and video roots before initialisation completes.
- Configuration survives restart and upgrade; uninstall keeps independent user data and videos.
- Start menu and desktop shortcuts enabled.
- `runAfterFinish=false`; first install is launched by the user, while one-click updates explicitly restart through the verified handoff.
- Silent install supports `/S /D=<absolute-install-directory>`.
- `deleteAppDataOnUninstall` must remain `false`.
- Do not add custom NSIS scripts that modify PATH, system Python, system FFmpeg or browser settings.

## User Data

Production user data is outside the installation directory:

- `app.getPath('userData')/storage-settings.json` is a bootstrap file containing only the selected roots.
- On first production launch, the user selects an application-data root and a separate video-download root.
- Cookies, Profile, CSV, queue, database and logs live under the selected application-data root.
- Downloaded videos live under the selected video-download root and never inside the installation directory.
- Development runs keep their legacy project paths and do not consume production storage settings.
- Application-data migration stops Backend, copies and SHA256-verifies data, switches configuration, restarts Backend and verifies the effective FastAPI paths. Failure restores the previous configuration.
- Video-directory changes do not move existing videos; each download task keeps the target directory captured when it was queued.
- Uninstall must not remove user-selected data or video roots.

Install, upgrade and uninstall tests must use temporary user-data directories. Never use real user data for acceptance testing.

## Signing

U1.3 configures production update-manifest signing with Ed25519 key `douyin-release-2026-01`. Only its public key is committed in `frontend/update/keyring.json`; the private key is stored outside the repository and must never be packaged or uploaded.

Code signing is not configured in this repository.

Production signing must use externally supplied organization credentials such as Electron Builder-supported certificate environment variables. Never commit certificate files, private keys or passwords.

Until a valid certificate is configured, the installer, application, Backend and FFmpeg remain unsigned. Unsigned releases must not be represented as trusted or signed.

## Icons

The user-supplied portrait at `build/icon-source.png` is the official icon source. `build/icon.ico` contains 16, 20, 24, 32, 40, 48, 64, 128 and 256 pixel entries. `build/icon.png` is used by BrowserWindow and `public/app-icon.png`/`public/favicon.png` provide the in-app and web icon variants.

Keep the application EXE, NSIS installer, uninstaller, desktop shortcut and start menu icon aligned to `build/icon.ico`. Do not replace the supplied portrait without brand-owner approval.

## Licensing

`release/RELEASE-MANIFEST.json` and `release/THIRD-PARTY-NOTICES.txt` are generated from real package metadata and build resources.

Before public distribution:

- Keep FFmpeg `LICENSE.txt`, `BUILD-INFO.json`, `VERSION.txt` and `SOURCE-INFO.txt` with the installed resources.
- Preserve Electron and Chromium license files.
- Review generated Node and Python dependency licenses.
- Maintain a valid GPLv3 corresponding-source access method for the exact FFmpeg build and its statically linked GPL dependencies.
- Do not claim the source-offer obligation is complete until the release owner has established and verified that process.

## Upgrade

U1.1 does not ship `Updater.exe`, does not download updates and does not replace program files. The Launcher/versioned-directory/Updater implementation starts in U1.2 and U1.3.

U1.3 completes one-click full-NSIS updates. Electron requires an idle Backend, stops RuntimeServices through the FastAPI shutdown endpoint, waits for the owned Backend process to exit, and launches a short-lived PowerShell handoff. After the Electron PID exits, the handoff runs the verified NSIS installer with `/S` and no `/D`, verifies installer exit code and installed version metadata, writes `update-result.json`, and restarts the new application. The restarted application rechecks Backend Health and reports success or failure in Settings.

The public update source is `lyx123-c/q`. It must remain public for anonymous client updates. The existing legacy Release tagged `0.1` is not a valid update source and is not overwritten.

The NSIS installer uses the stable product/app identity. The official `v0.2.0` release upgrades an isolated `0.1.0` baseline in-place while preserving external user data.

Do not introduce a second installer framework or automatic updater without a separate design and security review.

## Acceptance

At minimum verify:

- Silent installation into a temporary directory.
- Start menu and desktop shortcut targets.
- Installed Electron, Bridge, Vue and Backend health.
- Installed Chromium launch.
- Installed FFmpeg launch and synthetic media merge.
- Same-version reinstall.
- Upgrade with unchanged user-data files.
- Silent uninstall removes program files and shortcuts.
- User data survives uninstall.
- No residual application, Backend or FFmpeg process.
- Backend port released.
- Full Python test suite passes.
