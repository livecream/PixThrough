"""앱 자동 업데이트: GitHub 최신 릴리스 확인 → 내 OS용 zip 받기 → 앱 교체 → 다시 시작.

Qt에 의존하지 않는 부분만 모았다(화면은 gui_update.py). 흐름은:

1. fetch_latest_release: GitHub API에서 최신 릴리스와 내 OS용 zip(PixThrough-macOS.zip /
   PixThrough-Windows.zip)을 찾는다.
2. find_install_target: 지금 실행 중인 앱이 어디 있는지, 스스로 교체할 수 있는지(패키징된
   앱인지, 폴더에 쓸 수 있는지, macOS가 임시 위치에서 실행 중인지) 판단한다.
3. download_and_stage: zip을 받아 GitHub이 알려준 SHA-256으로 검증하고, 설치 위치와 같은
   폴더의 임시 폴더(.PixThrough-update)에 풀어 둔다 — 같은 디스크여야 이동이 바로 된다.
4. launch_installer: 앱이 꺼지기를 기다렸다가 기존 앱을 백업 이름으로 옮기고, 새 앱을
   제자리에 넣고, 백업을 지우고, 새 앱을 실행하는 작은 스크립트(macOS bash / Windows
   PowerShell)를 띄운다. 호출한 쪽은 바로 앱을 종료한다. 교체 중 실패하면 백업을 되돌려
   예전 앱을 다시 실행한다.

설정·마지막 폴더(QSettings)와 저장된 점수·얼굴 모델(앱 데이터 폴더)은 앱 밖에 있어
교체해도 그대로 남는다. 사진 파일은 전혀 건드리지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LATEST_RELEASE_API = "https://api.github.com/repos/livecream/PixThrough/releases/latest"
RELEASES_PAGE = "https://github.com/livecream/PixThrough/releases/latest"
STAGING_DIR_NAME = ".PixThrough-update"
BACKUP_SUFFIX = ".update-backup"

_ASSET_NAMES = {"darwin": "PixThrough-macOS.zip", "win32": "PixThrough-Windows.zip"}


class UpdateError(Exception):
    """업데이트를 진행할 수 없을 때. 메시지는 사용자에게 그대로 보여준다."""


class UpdateCancelled(Exception):
    """다운로드 중 사용자가 취소했을 때."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str  # "1.3.0" (태그의 v는 뗌)
    notes: str
    page_url: str
    asset_name: str | None  # 내 OS용 zip이 없으면 None (그때는 페이지만 열어 줌)
    asset_url: str | None
    asset_size: int
    sha256: str | None  # GitHub이 계산해 둔 zip 해시. 있으면 받은 파일과 비교


@dataclass(frozen=True)
class InstallTarget:
    path: Path  # 교체할 대상: macOS는 PixThrough.app, Windows는 PixThrough.exe가 든 폴더
    executable_name: str  # Windows에서 다시 실행할 exe 이름
    problem: str | None  # 스스로 교체할 수 없는 이유 (None이면 가능)


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v1.10.0"/"1.10.0" → (1, 10, 0). 숫자 세 개가 아니면 None."""
    parts = text.strip().lstrip("vV").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(latest: str, current: str) -> bool:
    latest_v, current_v = parse_version(latest), parse_version(current)
    if latest_v is None or current_v is None:
        return False
    return latest_v > current_v


def _ssl_contexts() -> list[ssl.SSLContext]:
    """패키징된 앱에서는 OS 인증서 위치를 못 찾을 수 있어 certifi 묶음을 먼저 쓰고, 회사망
    프록시처럼 OS에만 등록된 인증서가 필요한 환경을 위해 OS 기본 설정으로도 한 번 더 시도한다."""
    contexts = []
    try:
        import certifi

        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    contexts.append(ssl.create_default_context())
    return contexts


def _open(url: str, timeout: float):
    request = urllib.request.Request(
        url, headers={"User-Agent": "PixThrough-updater", "Accept": "application/vnd.github+json"}
    )
    contexts = _ssl_contexts()
    for i, context in enumerate(contexts):
        try:
            return urllib.request.urlopen(request, timeout=timeout, context=context)
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError) and i + 1 < len(contexts):
                continue
            raise


def parse_release(data: dict, platform: str = sys.platform) -> ReleaseInfo:
    version = str(data.get("tag_name", "")).lstrip("vV")
    if parse_version(version) is None:
        raise UpdateError(f"최신 릴리스의 버전 형식을 알 수 없습니다: {data.get('tag_name')!r}")
    wanted = _ASSET_NAMES.get(platform)
    asset = next((a for a in data.get("assets", []) if a.get("name") == wanted), None)
    digest = str(asset.get("digest") or "") if asset else ""
    return ReleaseInfo(
        version=version,
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        asset_name=asset.get("name") if asset else None,
        asset_url=asset.get("browser_download_url") if asset else None,
        asset_size=int(asset.get("size") or 0) if asset else 0,
        sha256=digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else None,
    )


def fetch_latest_release(timeout: float = 10.0) -> ReleaseInfo:
    try:
        with _open(LATEST_RELEASE_API, timeout) as response:
            data = json.load(response)
    except Exception as e:
        raise UpdateError(f"최신 버전 정보를 가져오지 못했습니다: {e}") from e
    return parse_release(data)


def find_install_target(
    executable: str | None = None,
    frozen: bool | None = None,
    platform: str = sys.platform,
) -> InstallTarget:
    """지금 실행 중인 앱의 위치와, 스스로 교체할 수 있는지를 판단한다."""
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = Path(executable)
    if not frozen:
        return InstallTarget(exe, exe.name, "소스 코드로 실행 중이라 자동 업데이트를 할 수 없습니다(git pull로 업데이트).")
    if platform == "darwin":
        # .../PixThrough.app/Contents/MacOS/PixThrough
        bundle = exe.parents[2] if len(exe.parents) >= 3 else exe.parent
        if bundle.suffix != ".app":
            return InstallTarget(bundle, exe.name, "앱 위치를 찾지 못했습니다.")
        if "AppTranslocation" in str(bundle):
            return InstallTarget(
                bundle,
                exe.name,
                "macOS가 앱을 임시 위치에서 실행하고 있어 교체할 수 없습니다. "
                "PixThrough.app을 응용 프로그램(Applications) 폴더 등으로 옮긴 뒤 다시 실행하세요.",
            )
        target = bundle
    elif platform == "win32":
        target = exe.parent
    else:
        return InstallTarget(exe.parent, exe.name, "이 운영체제는 자동 업데이트를 지원하지 않습니다.")
    if not os.access(target.parent, os.W_OK):
        return InstallTarget(
            target,
            exe.name,
            f"앱이 있는 폴더({target.parent})에 쓸 권한이 없어 교체할 수 없습니다.",
        )
    return InstallTarget(target, exe.name, None)


def _download(
    release: ReleaseInfo,
    dest: Path,
    on_progress: Callable[[int, int], None] | None,
    should_cancel: Callable[[], bool] | None,
    timeout: float,
) -> None:
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(release.asset_url, timeout) as response, open(dest, "wb") as out:
            total = int(response.headers.get("Content-Length") or release.asset_size or 0)
            while True:
                if should_cancel is not None and should_cancel():
                    raise UpdateCancelled
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, total)
    except (UpdateCancelled, UpdateError):
        raise
    except Exception as e:
        raise UpdateError(f"새 버전을 받는 중 오류가 났습니다: {e}") from e
    if release.sha256 and digest.hexdigest() != release.sha256:
        raise UpdateError("받은 파일이 릴리스와 다릅니다(해시 불일치). 다시 시도해 주세요.")


def _extract(zip_path: Path, staging: Path, platform: str) -> None:
    if platform == "darwin":
        # .app 안의 심볼릭 링크·실행 권한은 zipfile이 살리지 못해 macOS 기본 도구로 푼다
        result = subprocess.run(
            ["ditto", "-x", "-k", str(zip_path), str(staging)], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise UpdateError(f"압축을 풀지 못했습니다: {result.stderr.strip()}")
    else:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(staging)


def _staged_app(staging: Path, target: InstallTarget, platform: str) -> Path:
    """풀어 둔 폴더에서 새 앱(맨 위 항목 하나)을 찾고, 실행 파일이 들어 있는지 확인한다."""
    entries = [p for p in staging.iterdir() if not p.name.startswith(".") and p.name != "__MACOSX"]
    if len(entries) != 1 or not entries[0].is_dir():
        raise UpdateError("받은 파일의 구성이 예상과 다릅니다.")
    app = entries[0]
    if platform == "darwin":
        expected = app / "Contents" / "MacOS" / target.executable_name
    else:
        expected = app / target.executable_name
    if not expected.is_file():
        raise UpdateError(f"받은 파일에 실행 파일이 없습니다: {expected.relative_to(staging)}")
    return app


def download_and_stage(
    release: ReleaseInfo,
    target: InstallTarget,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    platform: str = sys.platform,
    timeout: float = 30.0,
) -> Path:
    """새 버전을 받아 검증하고 설치 위치 옆 임시 폴더에 풀어 둔 뒤, 새 앱 경로를 돌려준다."""
    if target.problem:
        raise UpdateError(target.problem)
    if not release.asset_url:
        raise UpdateError("이 운영체제용 파일이 릴리스에 없습니다.")
    staging = target.path.parent / STAGING_DIR_NAME
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    download_dir = Path(tempfile.mkdtemp(prefix="PixThrough-update-"))
    try:
        zip_path = download_dir / (release.asset_name or "update.zip")
        _download(release, zip_path, on_progress, should_cancel, timeout)
        _extract(zip_path, staging, platform)
        return _staged_app(staging, target, platform)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


# 앱이 꺼진 뒤 교체를 맡는 스크립트. 경로는 모두 인자로 넘겨 따옴표 문제를 피한다.
# 인자: pid target staged backup staging log launch(1/0)
MAC_INSTALL_SCRIPT = r"""#!/bin/bash
pid="$1"; target="$2"; staged="$3"; backup="$4"; staging="$5"; log="$6"; launch="$7"
exec >>"$log" 2>&1
echo "$(date) 업데이트 시작: $target"
for _ in $(seq 1 600); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
if kill -0 "$pid" 2>/dev/null; then echo "앱이 종료되지 않아 중단"; exit 1; fi
relaunch() { [ "$launch" = "1" ] && open "$1"; }
rm -rf "$backup"
if ! mv "$target" "$backup"; then echo "기존 앱을 옮기지 못해 중단"; relaunch "$target"; exit 1; fi
if ! mv "$staged" "$target"; then
  echo "새 앱을 넣지 못해 되돌림"; mv "$backup" "$target"; relaunch "$target"; exit 1
fi
xattr -dr com.apple.quarantine "$target" 2>/dev/null
rm -rf "$backup" "$staging"
echo "$(date) 업데이트 완료"
relaunch "$target"
exit 0
"""

WINDOWS_INSTALL_SCRIPT = r"""param(
  [int]$ProcId, [string]$Target, [string]$Staged, [string]$Backup,
  [string]$Staging, [string]$Log, [string]$Exe, [int]$Launch = 1
)
function Write-Log($m) { Add-Content -LiteralPath $Log -Value "$(Get-Date -Format o) $m" -Encoding UTF8 }
function Relaunch() { if ($Launch -eq 1) { Start-Process -FilePath (Join-Path $Target $Exe) -WorkingDirectory $Target } }
Write-Log "update start: $Target"
try { Wait-Process -Id $ProcId -Timeout 60 -ErrorAction Stop } catch {}
if (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) { Write-Log "app still running, abort"; exit 1 }
Start-Sleep -Milliseconds 500
if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }
$moved = $false
for ($i = 0; $i -lt 20; $i++) {
  try { Move-Item -LiteralPath $Target -Destination $Backup -ErrorAction Stop; $moved = $true; break }
  catch { Start-Sleep -Milliseconds 500 }
}
if (-not $moved) { Write-Log "could not move old app, abort"; Relaunch; exit 1 }
try { Move-Item -LiteralPath $Staged -Destination $Target -ErrorAction Stop }
catch {
  Write-Log "could not install new app, restoring: $_"
  Move-Item -LiteralPath $Backup -Destination $Target
  Relaunch; exit 1
}
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
Write-Log "update done"
Relaunch
exit 0
"""


def installer_command(
    target: InstallTarget,
    staged: Path,
    pid: int,
    log_path: Path,
    launch: bool = True,
    platform: str = sys.platform,
) -> list[str]:
    """교체 스크립트를 임시 폴더에 쓰고, 그것을 실행할 명령을 돌려준다."""
    script_dir = Path(tempfile.mkdtemp(prefix="PixThrough-installer-"))
    backup = target.path.with_name(target.path.name + BACKUP_SUFFIX)
    staging = staged.parent
    if platform == "win32":
        script = script_dir / "install_update.ps1"
        # Windows PowerShell 5.1은 BOM이 없으면 한글 경로를 잘못 읽는다
        script.write_text(WINDOWS_INSTALL_SCRIPT, encoding="utf-8-sig")
        return [
            "powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-ProcId", str(pid), "-Target", str(target.path), "-Staged", str(staged),
            "-Backup", str(backup), "-Staging", str(staging), "-Log", str(log_path),
            "-Exe", target.executable_name, "-Launch", "1" if launch else "0",
        ]
    script = script_dir / "install_update.sh"
    script.write_text(MAC_INSTALL_SCRIPT, encoding="utf-8")
    return [
        "/bin/bash", str(script), str(pid), str(target.path), str(staged), str(backup),
        str(staging), str(log_path), "1" if launch else "0",
    ]


def launch_installer(
    target: InstallTarget, staged: Path, log_path: Path, platform: str = sys.platform
) -> None:
    """교체 스크립트를 앱과 분리된 프로세스로 띄운다. 호출한 쪽은 곧바로 앱을 종료해야 한다."""
    command = installer_command(target, staged, os.getpid(), log_path, platform=platform)
    env = dict(os.environ)
    # 패키징된 앱이 띄운 프로세스가 새 앱을 실행할 때 예전 앱의 PyInstaller 환경을 물려받지 않게
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    kwargs: dict = {"cwd": tempfile.gettempdir(), "env": env, "close_fds": True}
    if platform == "win32":
        # 콘솔 창 없이, 앱이 꺼져도 살아남는 별도 프로세스로
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
