# -*- mode: python -*-

# .spec files can be tricky, so here are som useful resources:
#
#  - https://pythonhosted.org/PyInstaller/spec-files.html
#  - https://shanetully.com/2013/08/cross-platform-deployment-of-python-applications-with-pyinstaller/

import platform
import os
import sys


extra_pathex = []
if platform.system() == "Windows":
	# The Windows version includes paths to Qt binaries which are
	# not automatically found due to bug in PyInstaller 3.2.
	# See: https://github.com/pyinstaller/pyinstaller/issues/2152
	import PyQt5
	pyqt_path = os.path.dirname(PyQt5.__file__)
	extra_pathex.append(pyqt_path + "\\Qt\\bin")


icon = 'media/logo/logo.ico'
block_cipher = None


a = Analysis(['aw_qt/__main__.py'],
             pathex=[] + extra_pathex,
             binaries=None,
             datas=[('resources/aw-qt.desktop', '.')],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='aw-qt',
          debug=False,
          strip=False,
          upx=True,
          icon=icon,
          console=False if platform.system() == "Windows" else True)
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name='aw-qt')


# Build a .app for macOS
# This would probably be done best by also bundling aw-server, aw-watcher-afk and
# aw-watcher-window in one single `.app`.
#
# NOTE: Untested, remove the False to test
if False and platform.system() == "Darwin":
    app = BUNDLE(exe,
                 name="ActivityWatch.app",
                 icon=None)  # TODO: Should this be icon=icon?
