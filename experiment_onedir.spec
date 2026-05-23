# -*- mode: python ; coding: utf-8 -*-
# Сборка «в папку» (onedir): запуск быстрый на любом ПК, без распаковки в %TEMP%.
# Результат: dist\Experiment\Experiment.exe (+ папка _internal).
# Переносить на другой ПК нужно всю папку Experiment.

block_cipher = None

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('paintings', 'paintings'),
    ('PyOpenGaze', 'PyOpenGaze'),
    ('calib_painting.jpg', '.'),
]

hiddenimports = [
    'flask', 'werkzeug', 'werkzeug.routing', 'jinja2', 'PIL',
    'mss', 'mss.windows', 'imageio_ffmpeg', 'cv2', 'webview',
    'pandas', 'numpy', 'scipy',
    'scipy.ndimage',       # gaussian_filter в analysis_paintings.py
    'scipy.stats',
    'openpyxl',            # pandas ExcelWriter backend (post_process_paintings)
    'matplotlib',          # тепловые карты и траектории в analysis_paintings.py
    'matplotlib.pyplot',
    'matplotlib.backends.backend_agg',
]

excludes = ['PyQt5', 'PyQt6', 'PySide2', 'PySide6']

a = Analysis(
    ['experiment.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_startup_log.py'],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Experiment',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Experiment',
)
