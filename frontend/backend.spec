# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

spec_dir = Path(SPECPATH)
source_root = spec_dir.parent

datas = []
binaries = []
hiddenimports = []

for package in ("patchright", "curl_cffi"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in ("apify_fingerprint_datapoints", "browserforge", "certifi", "tldextract"):
    datas += collect_data_files(package)

hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "scrapling.fetchers.stealth_chrome",
    "scrapling.engines._browsers._base",
    "scrapling.engines._browsers._network",
    "scrapling.engines._browsers._page",
    "scrapling.engines._browsers._stealth",
    "scrapling.engines._browsers._types",
    "scrapling.engines.toolbelt.convertor",
    "scrapling.engines.toolbelt.custom",
    "scrapling.engines.toolbelt.fingerprints",
    "scrapling.engines.toolbelt.navigation",
]

analysis = Analysis(
    [str(source_root / "backend_server.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "Django",
        "django",
        "matplotlib",
        "openai",
        "pygame",
        "pyarrow",
        "selenium",
        "streamlit",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Backend",
)
