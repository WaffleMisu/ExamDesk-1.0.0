# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).parent
src_root = project_root / "src"

a = Analysis(
    [str(src_root / "examdesk" / "__main__.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=[
        (str(project_root / "templates" / "ExamDesk_题库维护模板.xlsx"), "模板"),
        (str(project_root / "docs" / "使用说明.txt"), "."),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(project_root / "PRIVACY.md"), "."),
        (str(project_root / "licenses"), "licenses"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ExamDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "packaging" / "app.ico"),
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ExamDesk",
)
