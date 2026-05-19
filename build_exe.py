"""PyInstaller build script for DebManager.

Build executable:
    python build_exe.py

Or directly with PyInstaller:
    pyinstaller build_exe.py --clean --noconsole
"""

import sys
import os
from pathlib import Path

# Ensure PyInstaller is available
try:
    import PyInstaller
except ImportError:
    print("PyInstaller not found. Install it with: pip install pyinstaller")
    sys.exit(1)

from PyInstaller.__main__ import run

# Project paths
ROOT = Path(os.path.dirname(os.path.abspath(__file__)))

# Ensure output dir exists
dist_dir = ROOT / 'dist'
build_dir = ROOT / 'build'

# PyInstaller arguments
args = [
    '--name=DebManager',
    '--onefile',                    # Single executable file
    '--console',                    # Show console window for debugging
    '--clean',                      # Clean cache
    '--add-data', f'{ROOT / "templates"}{os.pathsep}templates',
    '--distpath', str(dist_dir),
    '--workpath', str(build_dir),
    '--specpath', str(ROOT),
    # Hidden imports for wsgidav and dependencies
    '--hidden-import=wsgidav',
    '--hidden-import=wsgidav.wsgidav_app',
    '--hidden-import=wsgidav.fs_dav_provider',
    '--hidden-import=wsgidav.server.server_cli',
    '--hidden-import=cheroot',
    '--hidden-import=cheroot.wsgi',
    '--hidden-import=waitress',
    # Application entry point
    str(ROOT / 'main.py'),
]

if __name__ == '__main__':
    print('=' * 55)
    print('  Building DebManager executable...')
    print('=' * 55)
    print(f'  Source: {ROOT}')
    print(f'  Output: {dist_dir}')
    print('=' * 55)
    run(args)
    print('\n' + '=' * 55)
    print('  Build complete!')
    print(f'  Executable: {dist_dir / "DebManager.exe"}')
    print('=' * 55)
