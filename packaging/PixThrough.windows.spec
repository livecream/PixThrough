# -*- mode: python ; coding: utf-8 -*-
"""Windows용 PyInstaller 스펙. macOS용 PixThrough.spec과 거의 동일하지만,
macOS 전용인 BUNDLE(.app 번들) 단계가 없다 — Windows는 COLLECT가 만든
폴더(PixThrough.exe + 의존 파일들)가 배포 단위다."""

import sys as _sys
import os as _os

# SPECPATH는 PyInstaller가 이 파일의 실행 네임스페이스에 주입하는 값으로,
# 실행한 위치(cwd)와 무관하게 이 스펙 파일 자신이 있는 폴더를 가리킨다.
_src_dir = _os.path.normpath(_os.path.join(SPECPATH, "..", "src"))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

from version import __version__

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = collect_data_files("mediapipe") + collect_data_files("cv2")
hiddenimports = (
    collect_submodules("mediapipe")
    + collect_submodules("cv2")
    + ["PIL._tkinter_finder", "rawpy"]
)

a = Analysis(
    ["../src/gui.py"],
    binaries=collect_dynamic_libs("rawpy"),  # LibRaw (RAW 미리보기 추출)
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixThrough",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PixThrough",
)
