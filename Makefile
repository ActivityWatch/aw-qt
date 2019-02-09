.PHONY: build install test test-integration typecheck package clean

pip_install_args := . --upgrade

ifdef DEV
pip_install_args += --editable
endif

build: aw_qt/resources.py
	pip3 install mypy
	pip3 install $(pip_install_args)

install:
	bash scripts/config-autostart.sh

test:
	python3 -c 'import aw_qt'

test-integration:
	python3 ./tests/integration_tests.py --no-modules

typecheck:
	mypy aw_qt --ignore-missing-imports

precommit:
	make typecheck
	make test
	make test-integration

package:
	pyinstaller --clean --noconfirm --windowed aw-qt.spec

clean:
	rm -rf build dist
	rm -rf __pycache__ aw_qt/__pycache__

aw_qt/resources.py: aw_qt/resources.qrc
	pip3 install 'pyqt5<5.11'
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
