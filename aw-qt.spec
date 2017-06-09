# -*- mode: python -*-

import platform
import os

extra_pathex = []
if platform.system() == "Windows":
	# The Windows version includes paths to Qt binaries which are
	# not automatically found due to bug in PyInstaller 3.2.
	# See: https://github.com/pyinstaller/pyinstaller/issues/2152
	import PyQt5
	pyqt_path = os.path.dirname(PyQt5.__file__)
	extra_pathex.append(pyqt_path + "\\Qt\\bin")


block_cipher = None



a = Analysis(['aw_qt/__main__.py'],
             pathex=[] + extra_pathex,
             binaries=None,
             datas=None,
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
          console=True )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name='aw-qt')
