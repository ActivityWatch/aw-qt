.PHONY: build install test test-integration typecheck package clean

pip_install_args := . --upgrade --process-dependency-links

ifdef DEV
pip_install_args := --editable $(pip_install_args)
endif

build:
	pip3 install pyqt5 mypy
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
	pip3 install $(pip_install_args)

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
