# -*- mode: python ; coding: utf-8 -*-
import sys as _sys
import os as _os

# Add src directory to path for importing version module
# SPECPATH is injected by PyInstaller into this file's exec namespace and is
# the spec file's own directory, independent of the caller's cwd.
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

app = BUNDLE(
    coll,
    name="PixThrough.app",
    bundle_identifier="com.pixthrough.app",
    info_plist={
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
    },
)
