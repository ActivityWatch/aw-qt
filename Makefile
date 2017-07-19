.PHONY: build install test test-integration typecheck package clean

build:
	pip3 install pyqt5 mypy
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
	python3 setup.py install

install:
	bash scripts/config-autostart.sh

test:
	python3 -c 'import aw_qt'

test-integration:
	python3 ./tests/integration_tests.py --no-modules

typecheck:
	mypy aw_qt --ignore-missing-imports

package:
	pyinstaller aw-qt.spec --clean --noconfirm --windowed

clean:
	rm -rf build dist
	rm -rf __pycache__ aw_qt/__pycache__
