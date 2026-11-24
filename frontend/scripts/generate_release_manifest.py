#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
from email.parser import BytesParser
from email.policy import default as email_policy
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = FRONTEND_ROOT.parent
RELEASE_ROOT = FRONTEND_ROOT / "release"
UNPACKED_ROOT = RELEASE_ROOT / "win-unpacked"

DIRECT_PYTHON_PACKAGES = (
    "fastapi",
    "uvicorn",
    "httpx",
    "scrapling",
    "patchright",
    "playwright",
    "curl_cffi",
    "browserforge",
    "apify-fingerprint-datapoints",
    "pandas",
    "numpy",
    "psutil",
    "requests",
    "lxml",
    "pydantic",
    "starlette",
    "anyio",
    "tldextract",
    "msgspec",
    "certifi",
    "orjson",
)

FORBIDDEN_DATA_NAMES = {
    "douyin_videos.csv",
    "video_analytics.csv",
    "download_queue.json",
}
FORBIDDEN_DATA_DIRS = {"data", "tasks", "logs", "browser-profile"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(FRONTEND_ROOT.resolve())).replace("\\", "/")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip().lower())


def license_value(meta) -> str:
    expressions = meta.get_all("License-Expression") or []
    if expressions:
        return "; ".join(str(item).strip() for item in expressions if str(item).strip())
    license_text = str(meta.get("License") or "").strip()
    if license_text:
        return license_text.splitlines()[0][:200]
    classifiers = [
        str(item).strip()
        for item in (meta.get_all("Classifier") or [])
        if str(item).strip().startswith("License ::")
    ]
    return "; ".join(classifiers)


def direct_python_licenses() -> list[dict]:
    records = []
    missing = []
    for name in DIRECT_PYTHON_PACKAGES:
        try:
            meta = metadata(name)
        except PackageNotFoundError:
            missing.append(name)
            continue
        records.append(
            {
                "name": meta.get("Name") or name,
                "version": version(name),
                "license": license_value(meta),
                "source": "runtime-metadata",
            }
        )
    if missing:
        raise RuntimeError(f"Missing direct Python packages: {', '.join(missing)}")
    return records


def packaged_python_licenses() -> list[dict]:
    internal = UNPACKED_ROOT / "resources" / "backend" / "_internal"
    records = []
    for info_dir in sorted(internal.glob("*.dist-info")):
        metadata_path = info_dir / "METADATA"
        if not metadata_path.is_file():
            continue
        with metadata_path.open("rb") as stream:
            meta = BytesParser(policy=email_policy).parse(stream)
        records.append(
            {
                "name": meta.get("Name") or info_dir.name.split("-")[0],
                "version": meta.get("Version") or "",
                "license": license_value(meta),
                "source": relative(metadata_path),
            }
        )
    return records


def merge_python_licenses(*groups: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        for record in group:
            key = canonical_name(record["name"])
            current = merged.get(key)
            if current is None:
                merged[key] = record
                continue
            if not current.get("license") and record.get("license"):
                current["license"] = record["license"]
            if record.get("version") and not current.get("version"):
                current["version"] = record["version"]
    return sorted(merged.values(), key=lambda item: canonical_name(item["name"]))


def node_licenses() -> list[dict]:
    lock_path = FRONTEND_ROOT / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    records = []
    for path, data in (lock.get("packages") or {}).items():
        if not path or data.get("dev") or not path.startswith("node_modules/"):
            continue
        if data.get("link"):
            continue
        name = data.get("name") or path.rsplit("node_modules/", 1)[-1]
        records.append(
            {
                "name": name,
                "version": data.get("version") or "",
                "license": data.get("license") or "",
                "source": "package-lock.json",
            }
        )
    return sorted(records, key=lambda item: canonical_name(item["name"]))


def find_installer(package: dict, product_name: str) -> Path:
    expected = RELEASE_ROOT / f"{product_name}-{package['version']}-x64.exe"
    if expected.is_file():
        return expected
    candidates = sorted(
        (path for path in RELEASE_ROOT.glob("*.exe") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("NSIS installer was not found in release directory.")
    return candidates[0]


def ensure_no_user_data() -> None:
    resources = UNPACKED_ROOT / "resources"
    for directory_name in FORBIDDEN_DATA_DIRS:
        candidate = resources / directory_name
        if candidate.exists():
            raise RuntimeError(f"Forbidden user data directory in release: {candidate}")
    for path in UNPACKED_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() in FORBIDDEN_DATA_NAMES:
            raise RuntimeError(f"Forbidden user data file in release: {path}")


def main() -> int:
    package = json.loads((FRONTEND_ROOT / "package.json").read_text(encoding="utf-8"))
    build = package.get("build") or {}
    product_name = build.get("productName") or package["name"]
    installer = find_installer(package, product_name)
    app_exe = UNPACKED_ROOT / f"{product_name}.exe"
    backend_exe = UNPACKED_ROOT / "resources" / "backend" / "Backend.exe"
    ffmpeg_exe = UNPACKED_ROOT / "resources" / "ffmpeg" / "ffmpeg.exe"
    app_asar = UNPACKED_ROOT / "resources" / "app.asar"
    version_metadata_path = UNPACKED_ROOT / "resources" / "version-metadata.json"
    update_keyring_path = UNPACKED_ROOT / "resources" / "update" / "keyring.json"
    update_source_path = UNPACKED_ROOT / "resources" / "update" / "update-source.json"
    window_icon = UNPACKED_ROOT / "resources" / "icon.png"
    source_icon = FRONTEND_ROOT / "build" / "icon.ico"
    source_icon_png = FRONTEND_ROOT / "build" / "icon.png"
    for required in (
        app_exe,
        backend_exe,
        ffmpeg_exe,
        app_asar,
        version_metadata_path,
        update_keyring_path,
        update_source_path,
        window_icon,
        source_icon,
        source_icon_png,
    ):
        if not required.is_file():
            raise RuntimeError(f"Required release resource is missing: {required}")

    ensure_no_user_data()

    ffmpeg_info_path = UNPACKED_ROOT / "resources" / "ffmpeg" / "BUILD-INFO.json"
    ffmpeg_info = json.loads(ffmpeg_info_path.read_text(encoding="utf-8"))
    backend_runtime_path = (
        UNPACKED_ROOT / "resources" / "backend" / "runtime-manifest.json"
    )
    backend_runtime = json.loads(backend_runtime_path.read_text(encoding="utf-8-sig"))
    version_metadata = json.loads(version_metadata_path.read_text(encoding="utf-8"))
    update_keyring = json.loads(update_keyring_path.read_text(encoding="utf-8"))
    update_source = json.loads(update_source_path.read_text(encoding="utf-8"))
    if version_metadata.get("version") != package["version"]:
        raise RuntimeError("Version metadata does not match package.json.")
    if version_metadata.get("product_id") != build.get("appId"):
        raise RuntimeError("Version metadata product_id does not match appId.")
    if update_keyring.get("schema_version") != 1 or not isinstance(update_keyring.get("keys"), list):
        raise RuntimeError("Update public keyring is invalid.")
    if any("private" in key for key in update_keyring["keys"]):
        raise RuntimeError("Private key material must never be packaged.")
    if update_source.get("provider") != "github_releases":
        raise RuntimeError("Unsupported update source provider.")
    if (
        update_source.get("repository") == "lyx123-c/douyin-scrapy"
        and update_source.get("allow_development_repository_as_update_source") is not True
    ):
        raise RuntimeError(
            "Development repository requires explicit public update-source approval."
        )

    python_packages = merge_python_licenses(
        direct_python_licenses(),
        packaged_python_licenses(),
    )
    node_packages = node_licenses()

    electron_license = UNPACKED_ROOT / "LICENSE.electron.txt"
    chromium_license = UNPACKED_ROOT / "LICENSES.chromium.html"
    if not electron_license.is_file() or not chromium_license.is_file():
        raise RuntimeError("Electron or Chromium license files are missing.")

    signing_configured = bool(
        os.environ.get("CSC_LINK")
        or os.environ.get("WIN_CSC_LINK")
        or (build.get("win") or {}).get("certificateFile")
    )
    manifest = {
        "schema": 1,
        "application": {
            "name": product_name,
            "packageName": package["name"],
            "version": package["version"],
            "appId": build.get("appId"),
            "electronVersion": package["devDependencies"]["electron"],
            "electronBuilderVersion": package["devDependencies"]["electron-builder"],
            "icon": {
                "sourceIco": relative(source_icon),
                "sourcePng": relative(source_icon_png),
                "icoSha256": sha256_file(source_icon),
                "pngSha256": sha256_file(source_icon_png),
            },
        },
        "installer": {
            "path": relative(installer),
            "bytes": installer.stat().st_size,
            "sha256": sha256_file(installer),
        },
        "release": {
            "unpackedPath": relative(UNPACKED_ROOT),
            "bytes": sum(
                path.stat().st_size for path in UNPACKED_ROOT.rglob("*") if path.is_file()
            ),
        },
        "resources": {
            "appExe": relative(app_exe),
            "backendExe": relative(backend_exe),
            "appAsar": relative(app_asar),
            "ffmpegExe": relative(ffmpeg_exe),
            "windowIcon": relative(window_icon),
            "appAsarSha256": sha256_file(app_asar),
            "backendExeSha256": sha256_file(backend_exe),
            "ffmpegExeSha256": sha256_file(ffmpeg_exe),
            "windowIconSha256": sha256_file(window_icon),
        },
        "backendRuntime": backend_runtime,
        "versionProtocol": version_metadata,
        "updateSource": {
            "provider": update_source.get("provider"),
            "repository": update_source.get("repository") or None,
            "configured": bool(update_source.get("repository")),
            "manifestAsset": update_source.get("manifest_asset"),
            "signatureAsset": update_source.get("signature_asset"),
        },
        "updateKeyring": {
            "schemaVersion": update_keyring.get("schema_version"),
            "activeKeyIds": [
                key.get("key_id")
                for key in update_keyring["keys"]
                if key.get("status") == "active"
            ],
            "revokedKeyIds": [
                key.get("key_id")
                for key in update_keyring["keys"]
                if key.get("status") == "revoked"
            ],
        },
        "ffmpeg": ffmpeg_info,
        "licenses": {
            "electron": {
                "file": relative(electron_license),
                "bytes": electron_license.stat().st_size,
            },
            "chromium": {
                "file": relative(chromium_license),
                "bytes": chromium_license.stat().st_size,
            },
            "nodePackages": node_packages,
            "pythonPackages": python_packages,
        },
        "signing": {
            "certificateConfigured": signing_configured,
            "installerSigned": None,
            "applicationSigned": None,
            "backendSigned": None,
            "ffmpegSigned": None,
        },
        "releaseReadiness": {
            "engineeringBuildComplete": True,
            "fullNsisUpdateImplemented": True,
            "officialIconPresent": source_icon.is_file(),
            "codeSigningConfigured": signing_configured,
            "updateProtocolReady": bool(update_source.get("repository") and any(
                key.get("status") == "active" for key in update_keyring["keys"]
            )),
            "gplSourceOfferVerified": False,
        },
    }

    output_path = RELEASE_ROOT / "RELEASE-MANIFEST.json"
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    notices = [
        f"{product_name} {package['version']} third-party notices",
        "",
        "FFmpeg: GPL-3.0-or-later",
        f"Version: {ffmpeg_info['version']}",
        "License file: resources/ffmpeg/LICENSE.txt",
        "Source information: resources/ffmpeg/SOURCE-INFO.txt",
        "",
        "Electron: MIT",
        f"License file: {relative(electron_license)}",
        "",
        "Chromium: third-party licenses",
        f"License file: {relative(chromium_license)}",
        "",
        "Node.js production packages:",
    ]
    notices.extend(
        f"- {item['name']} {item['version']}: {item['license'] or 'see package metadata'}"
        for item in node_packages
    )
    notices.append("")
    notices.append("Python runtime packages:")
    notices.extend(
        f"- {item['name']} {item['version']}: {item['license'] or 'see package metadata'}"
        for item in python_packages
    )
    notices.extend(
        [
            "",
            "The FFmpeg build enables GPLv3 components. A valid corresponding-source",
            "access method must be retained before public distribution.",
            "",
        ]
    )
    (RELEASE_ROOT / "THIRD-PARTY-NOTICES.txt").write_text(
        "\n".join(notices),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "manifest": str(output_path),
                "notices": str(RELEASE_ROOT / "THIRD-PARTY-NOTICES.txt"),
                "installer": str(installer),
                "installerBytes": installer.stat().st_size,
                "nodePackages": len(node_packages),
                "pythonPackages": len(python_packages),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
