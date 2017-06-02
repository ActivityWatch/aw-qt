.PHONY: build

build:
	pip install pyqt5
	pyrcc5 -o aw_qt/resources.py aw_qt/resources.qrc
	python3 setup.py install

install:
	bash scripts/config-autostart.sh

test:
	python3 -c 'import aw_qt'

package:
	pyinstaller aw-qt.spec --clean --noconfirm

package-nuitka:
	export PYTHONPATH="/usr/local/lib/python3.5/dist-packages:/usr/local/lib/python3.5/site-packages:/home/${USER}/.local/lib/python3.5/site-packages:/home/${USER}/Programming/activitywatch/aw-core"; \
	nuitka --python-version=3.5 --portable --python-flag=no_site --output-dir="build-nuitka" --plugin-enable=qt-plugins aw_qt/
