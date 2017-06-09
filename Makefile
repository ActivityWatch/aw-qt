.PHONY: build

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
	pyinstaller aw-qt.spec --clean --noconfirm --windowed


