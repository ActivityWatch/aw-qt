.PHONY: build

build:
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc

install:
	bash scripts/config-autostart.sh

package:
	pyinstaller aw-qt.spec --clean --noconfirm
