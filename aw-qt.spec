# -*- mode: python -*-

# .spec files can be tricky, so here are some useful resources:
#
#  - https://pythonhosted.org/PyInstaller/spec-files.html
#  - https://shanetully.com/2013/08/cross-platform-deployment-of-python-applications-with-pyinstaller/

import platform
import os
import sys
from datetime import datetime

WIN = platform.system() == "Windows"

VS_VERSION_INFO = """
VSVersionInfo(
  ffi=FixedFileInfo(
    # filevers and prodvers should be always a tuple with four
    # items: (1, 2, 3, 4)
    # Set not needed items to zero 0.
    filevers=%(ver_tup)r,
    prodvers=%(ver_tup)r,
    # Contains a bitmask that specifies the valid bits 'flags'r
    mask=0x0,
    # Contains a bitmask that specifies the Boolean attributes
    # of the file.
    flags=0x0,
    # The operating system for which this file was designed.
    # 0x4 - NT and there is no need to change it.
    OS=0x4,
    # The general type of file.
    # 0x1 - the file is an application.
    fileType=0x1,
    # The function of the file.
    # 0x0 - the function is not defined for this fileType
    subtype=0x0,
    # Creation date and time stamp.
    # NOTE: Nobody ever sets this: https://stackoverflow.com/q/67851414/965332
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904E4',
        [StringStruct(u'FileDescription', u'%(name)s'),
        StringStruct(u'FileVersion', u'%(ver_str)s'),
        StringStruct(u'InternalName', u'%(internal_name)s'),
        StringStruct(u'LegalCopyright', u'Copyright © %(year) ActivityWatch Contributors'),
        StringStruct(u'OriginalFilename', u'%(exe_name)s'),
        StringStruct(u'ProductName', u'%(name)s'),
        StringStruct(u'ProductVersion', u'%(ver_str)s')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1252])])
  ]
)"""


extra_pathex = []
if WIN:
	# The Windows version includes paths to Qt binaries which are
	# not automatically found due to bug in PyInstaller 3.2.
	# See: https://github.com/pyinstaller/pyinstaller/issues/2152
	import PyQt5
	pyqt_path = os.path.dirname(PyQt5.__file__)
	extra_pathex.append(pyqt_path + "\\Qt\\bin")

file_ext = '.exe' if WIN else ''


# Read version information on Windows.
# We need to construct a file_version_info.txt file for the Windows installer.
# Eventually we might want this for all modules, if worth the effort (its a bit messy...).
# Based on: https://github.com/Yubico/python-yubicommon/blob/master/yubicommon/setup/pyinstaller_spec.py
VERSION = None
if WIN:
    NAME = "ActivityWatch"
    INTERNAL_NAME = "aw-qt"  # TODO: fetch from package info
    VERSION = 'build/file_version_info.txt'
    # FIXME: Don't hardcode
    ver_str = "0.12.0"

    global int_or_zero  # Needed due to how this script is invoked

    def int_or_zero(v):
        try:
            return int(v)
        except ValueError:
            return 0

    ver_tup = tuple(int_or_zero(v) for v in ver_str.split('.'))
    # Windows needs 4-tuple.
    if len(ver_tup) < 4:
        ver_tup += (0,) * (4-len(ver_tup))
    elif len(ver_tup) > 4:
        ver_tup = ver_tup[:4]

    # Write version info.
    with open(VERSION, 'w') as f:
        f.write(VS_VERSION_INFO % {
            'name': NAME,
            'internal_name': INTERNAL_NAME,
            'ver_tup': ver_tup,
            'ver_str': ver_str,
            'exe_name': INTERNAL_NAME + file_ext,
            'year': datetime.now().year,
        })



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

# Needed to be removed due to https://github.com/ActivityWatch/activitywatch/issues/607#issuecomment-862187836
exclude_libs = ["libfontconfig", "libfreetype"]
a.binaries = [bin for bin in a.binaries if not any(bin[0].find(lib) >= 0 for lib in exclude_libs)]

pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='aw-qt',
          version=VERSION,
          debug=False,
          strip=False,
          upx=True,
          icon=icon,
          console=False if WIN else True)

# Sign the executable
# This is how it's done for the python-yubicommon package (linked above), should take some inspiration.
#if WIN:
#    os.system("signtool.exe sign /fd SHA256 /t http://timestamp.verisign.com/scripts/timstamp.dll \"%s\"" %
#            (exe.name))

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
