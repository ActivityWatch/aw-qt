.PHONY: build install test test-integration typecheck package clean

build:
	poetry install

install:
	bash scripts/config-autostart.sh

test:
	python -c 'import aw_qt'

test-integration:
	python ./tests/integration_tests.py --no-modules

lint:
	poetry run flake8 aw_qt --ignore=E501,E302,E305,E231 --per-file-ignores="__init__.py:F401"

typecheck:
	poetry run mypy aw_qt --pretty

precommit:
	make typecheck
	make test
	make test-integration

package:
	pyinstaller --clean --noconfirm aw-qt.spec

clean:
	rm -rf build dist
	rm -rf __pycache__ aw_qt/__pycache__

#aw_qt/resources.py: aw_qt/resources.qrc
#	poetry run pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
