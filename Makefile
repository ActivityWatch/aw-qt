.PHONY: build

build:
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
	python3 setup.py install

install:
	bash scripts/config-autostart.sh

package:
	pyinstaller aw-qt.spec --clean --noconfirm
