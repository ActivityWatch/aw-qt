.PHONY: build

ifeq ($(OS),Windows_NT)
	DETECTED_OS := Windows
else
	DETECTED_OS := $(shell uname -s)
endif

build:
	pip3 install pyqt5
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
	python3 setup.py install

install:
	bash scripts/config-autostart.sh

test:
	python3 -c 'import aw_qt'

test-integration:
	python3 ./tests/integration_tests.py --no-modules

package:
	# The Windows version includes paths to Qt binaries which are
	# not automatically found due to bug in PyInstaller 3.2.
	# See: https://github.com/pyinstaller/pyinstaller/issues/2152
	@if [ "$(DETECTED_OS)" = "Windows" ]; then\
		pyinstaller aw-qt.spec	--path "C:/Python35/Lib/site-packages/PyQt5/Qt/bin"\
								--path "C:/Python35/Lib/site-packages/PyQt5/Qt/lib"\
								--clean --noconfirm;\
	else\
		pyinstaller aw-qt.spec --clean --noconfirm;\
	fi
