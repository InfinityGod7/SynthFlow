; SynthFlow PyInstaller spec file
; Run: pyinstaller synthflow.spec

block_cipher = None

a = Analysis(
    ['synthflow.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'sounddevice',
        'soundfile',
        'openai',
        'keyboard',
        'pyperclip',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'numpy',
        'cffi',
        'pynput',
        'pynput.keyboard',
        'pynput.mouse',
        'tkinter',
        'tkinter.ttk',
        'configparser',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SynthFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window - runs silently
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Add your own .ico path here if desired
)
