"""자동 업데이트(updater.py): 버전 비교, 릴리스 해석, 설치 위치 판단, 다운로드 검증·풀기,
그리고 실제 교체 스크립트(macOS·Linux는 bash, Windows는 PowerShell)를 가짜 앱으로 돌려 본다.
CI가 macOS·Windows·Linux에서 모두 돌리므로 각 OS의 스크립트가 실제로 검증된다."""

import hashlib
import subprocess
import sys
import zipfile

import pytest

import updater
from updater import (
    InstallTarget,
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    download_and_stage,
    find_install_target,
    installer_command,
    is_newer,
    parse_release,
)


def test_is_newer_compares_numerically():
    assert is_newer("1.10.0", "1.9.0")
    assert is_newer("v1.2.1", "1.2.0")
    assert not is_newer("1.2.0", "1.2.0")
    assert not is_newer("1.1.9", "1.2.0")
    assert not is_newer("nightly", "1.2.0")


def _release_json(**overrides):
    data = {
        "tag_name": "v1.3.0",
        "body": "## 바뀐 점",
        "html_url": "https://github.com/livecream/PixThrough/releases/tag/v1.3.0",
        "assets": [
            {
                "name": "PixThrough-macOS.zip",
                "browser_download_url": "https://example.invalid/mac.zip",
                "size": 10,
                "digest": "sha256:" + "a" * 64,
            },
            {
                "name": "PixThrough-Windows.zip",
                "browser_download_url": "https://example.invalid/win.zip",
                "size": 20,
                "digest": None,
            },
        ],
    }
    data.update(overrides)
    return data


def test_parse_release_picks_asset_for_platform():
    mac = parse_release(_release_json(), platform="darwin")
    win = parse_release(_release_json(), platform="win32")

    assert mac.version == "1.3.0"
    assert mac.asset_url.endswith("mac.zip") and mac.sha256 == "a" * 64
    assert win.asset_url.endswith("win.zip") and win.sha256 is None
    assert parse_release(_release_json(), platform="linux").asset_url is None


def test_parse_release_rejects_odd_tag():
    with pytest.raises(UpdateError):
        parse_release(_release_json(tag_name="latest"), platform="darwin")


def test_install_target_from_source_cannot_self_update(tmp_path):
    target = find_install_target(str(tmp_path / "python"), frozen=False, platform="darwin")
    assert target.problem is not None


def test_install_target_mac_bundle(tmp_path):
    exe = tmp_path / "PixThrough.app" / "Contents" / "MacOS" / "PixThrough"
    target = find_install_target(str(exe), frozen=True, platform="darwin")

    assert target.path == tmp_path / "PixThrough.app"
    assert target.problem is None


def test_install_target_mac_translocated_is_refused(tmp_path):
    exe = tmp_path / "AppTranslocation" / "X" / "d" / "PixThrough.app" / "Contents" / "MacOS" / "PixThrough"
    target = find_install_target(str(exe), frozen=True, platform="darwin")
    assert "Applications" in target.problem


def test_install_target_windows_folder(tmp_path):
    exe = tmp_path / "PixThrough" / "PixThrough.exe"
    target = find_install_target(str(exe), frozen=True, platform="win32")

    assert target.path == tmp_path / "PixThrough"
    assert target.executable_name == "PixThrough.exe"
    assert target.problem is None


def _windows_zip(path, exe_name="PixThrough.exe", marker="new"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"PixThrough/{exe_name}", marker)
        archive.writestr("PixThrough/_internal/lib.txt", "lib")
    return path


def _release_for(zip_path, sha256=None):
    return ReleaseInfo(
        version="9.9.9",
        notes="",
        page_url="",
        asset_name=zip_path.name,
        asset_url=zip_path.as_uri(),
        asset_size=zip_path.stat().st_size,
        sha256=sha256,
    )


def _windows_target(tmp_path):
    app = tmp_path / "apps" / "PixThrough"
    app.mkdir(parents=True)
    (app / "PixThrough.exe").write_text("old")
    return InstallTarget(app, "PixThrough.exe", None)


def test_download_and_stage_verifies_and_extracts(tmp_path):
    zip_path = _windows_zip(tmp_path / "PixThrough-Windows.zip")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    target = _windows_target(tmp_path)
    progress = []

    staged = download_and_stage(
        _release_for(zip_path, sha), target, on_progress=lambda d, t: progress.append(d), platform="win32"
    )

    assert staged == target.path.parent / updater.STAGING_DIR_NAME / "PixThrough"
    assert (staged / "PixThrough.exe").read_text() == "new"
    assert progress and progress[-1] == zip_path.stat().st_size
    assert (target.path / "PixThrough.exe").read_text() == "old"  # 설치된 앱은 아직 그대로


def test_download_and_stage_rejects_hash_mismatch(tmp_path):
    zip_path = _windows_zip(tmp_path / "PixThrough-Windows.zip")
    target = _windows_target(tmp_path)

    with pytest.raises(UpdateError, match="해시"):
        download_and_stage(_release_for(zip_path, "0" * 64), target, platform="win32")
    assert not (target.path.parent / updater.STAGING_DIR_NAME).exists()


def test_download_and_stage_rejects_zip_without_app(tmp_path):
    zip_path = _windows_zip(tmp_path / "PixThrough-Windows.zip", exe_name="Other.exe")
    target = _windows_target(tmp_path)

    with pytest.raises(UpdateError, match="실행 파일"):
        download_and_stage(_release_for(zip_path), target, platform="win32")


def test_download_and_stage_can_be_cancelled(tmp_path):
    zip_path = _windows_zip(tmp_path / "PixThrough-Windows.zip")
    target = _windows_target(tmp_path)

    with pytest.raises(UpdateCancelled):
        download_and_stage(_release_for(zip_path), target, should_cancel=lambda: True, platform="win32")
    assert not (target.path.parent / updater.STAGING_DIR_NAME).exists()


def test_download_refuses_when_target_has_problem(tmp_path):
    zip_path = _windows_zip(tmp_path / "PixThrough-Windows.zip")
    target = InstallTarget(tmp_path, "PixThrough.exe", "권한 없음")

    with pytest.raises(UpdateError, match="권한 없음"):
        download_and_stage(_release_for(zip_path), target, platform="win32")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS의 ditto로 .app 풀기")
def test_download_and_stage_mac_app_keeps_bundle(tmp_path):
    app = tmp_path / "build" / "PixThrough.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PixThrough").write_text("new")
    (app / "Contents" / "MacOS" / "PixThrough").chmod(0o755)
    zip_path = tmp_path / "PixThrough-macOS.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(zip_path)], check=True)
    installed = tmp_path / "apps" / "PixThrough.app"
    (installed / "Contents" / "MacOS").mkdir(parents=True)
    target = InstallTarget(installed, "PixThrough", None)

    staged = download_and_stage(_release_for(zip_path), target, platform="darwin")

    exe = staged / "Contents" / "MacOS" / "PixThrough"
    assert exe.read_text() == "new"
    assert exe.stat().st_mode & 0o111  # 실행 권한 유지


def _finished_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _run_installer(tmp_path, target, staged):
    log = tmp_path / "update.log"
    platform = "win32" if sys.platform == "win32" else "darwin"
    command = installer_command(target, staged, _finished_pid(), log, launch=False, platform=platform)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    return result, log


def test_installer_script_swaps_app_and_cleans_up(tmp_path):
    """실제 교체 스크립트(이 OS용)를 꺼진 앱 PID로 돌려, 새 앱이 제자리에 들어가고 백업·임시
    폴더가 지워지는지 확인한다."""
    name = "PixThrough.app" if sys.platform != "win32" else "PixThrough"
    target_path = tmp_path / "설치 위치" / name  # 공백·한글 경로도 견디는지
    target_path.mkdir(parents=True)
    (target_path / "version.txt").write_text("old")
    staging = target_path.parent / updater.STAGING_DIR_NAME
    staged = staging / name
    staged.mkdir(parents=True)
    (staged / "version.txt").write_text("new")
    target = InstallTarget(target_path, "PixThrough.exe", None)

    result, log = _run_installer(tmp_path, target, staged)

    assert result.returncode == 0, (result.stdout, result.stderr, log.read_text("utf-8") if log.exists() else "")
    assert (target_path / "version.txt").read_text() == "new"
    assert not staging.exists()
    assert not target_path.with_name(name + updater.BACKUP_SUFFIX).exists()


def test_installer_script_restores_old_app_when_new_one_is_missing(tmp_path):
    name = "PixThrough.app" if sys.platform != "win32" else "PixThrough"
    target_path = tmp_path / name
    target_path.mkdir()
    (target_path / "version.txt").write_text("old")
    staged = tmp_path / updater.STAGING_DIR_NAME / name  # 만들지 않음 → 이동 실패
    target = InstallTarget(target_path, "PixThrough.exe", None)

    result, _ = _run_installer(tmp_path, target, staged)

    assert result.returncode != 0
    assert (target_path / "version.txt").read_text() == "old"
