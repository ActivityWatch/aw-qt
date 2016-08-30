from time import sleep
import logging
import subprocess
from subprocess import PIPE

from .gui.logviewer import LogViewer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aw.qt.manager")


class Module:
    def __init__(self, name, localdir=False):
        self._localdir = localdir
        self.name = name
        self.started = False
        self._process = None
        self._last_process = None
        self._log = ""

    def start(self, testing=False):
        logger.info("Starting module {}".format(self.name))
        exec_path = ("./" if self._localdir else "") + self.name + (" --testing" if testing else "")
        self._process = subprocess.Popen(exec_path, universal_newlines=True,
                                         stdout=PIPE, stderr=PIPE)
        self.started = True

    def stop(self):
        """
        Stops a module, and waits until it terminates.
        """
        # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
        if not self.started:
            logger.warning("Tried to kill module {}, but it has never been started".format(self.name))
        elif not self.is_alive():
            logger.warning("Tried to kill module {}, but it was already dead".format(self.name))
        else:
            logger.info("Stopping module {}".format(self.name))
            self._process.terminate()
            logger.info("Waiting for module {} to shut down".format(self.name))
            self._process.wait()
            logger.info("Module {} has shut down".format(self.name))
        assert not self.is_alive()
        self._last_process = self._process
        self._process = None
        self.started = False

    def is_alive(self):
        if not self.started:
            return False

        self._process.poll()
        # If returncode is none after p.poll(), module is still running
        return True if self._process.returncode is None else False

    def stderr(self):
        """Useful if you want to retrieve logs written to stderr"""
        if not self._process and not self._last_process:
            return "Module not started, no output available"
        elif self._last_process:
            log = self._last_process.stderr.read()
            self._log += log
        else:
            log = self._process.stderr.read()
            self._log += log
        return self._log

    def show_log(self):
        self.lv = LogViewer(name=self.name)
        self.lv.set_log(self.stderr())


_possible_modules = [
    "aw-server",
    "aw-watcher-afk",
    "aw-watcher-window",
    "aw-watcher-network"
]

# TODO: Filter away all modules not available on system
modules = [Module(name) for name in _possible_modules]


def get_unexpected_stops():
    return list(filter(lambda x: x.started and not x.is_alive(), modules))

if __name__ == "__main__":
    for module in modules:
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
