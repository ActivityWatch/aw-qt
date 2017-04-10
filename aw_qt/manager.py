import os
from time import sleep
import logging
import subprocess
from subprocess import PIPE

from .gui.logviewer import LogViewer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aw.qt.manager")


class Module:
    def __init__(self, name):
        self.name = name
        self.started = False
        self._process = None
        self._last_process = None
        self._log = ""

    def start(self, testing=False):
        logger.info("Starting module {}".format(self.name))

        # Will start module from localdir if present there,
        # otherwise will try to call what is available in PATH.
        exec_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), self.name)
        exec_cmd = [exec_path if os.path.isfile(exec_path) else self.name, "--testing" if testing else ""]

        WINDOWS=True
        if WINDOWS:
            exec_cmd = ["python", "-m", *exec_cmd]

        self._process = subprocess.Popen(exec_cmd, universal_newlines=True, bufsize=0,
                                         stdout=PIPE, stderr=subprocess.STDOUT)
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
        if self._last_process:
            print("Reading last process stderr...")
            log = self._last_process.stderr.read()
            self._log += log
        # FIXME: Currently causes everything to hang when trying to read stderr of self._process
        """
        if self._process:
            print("Reading active process stderr...")
            log = self._process.stderr.read()
            self._log += log
        """
        self._log += "\n\nReading the output of a currently running module is currently broken, sorry."
        return self._log

    def show_log(self):
        self.lv = LogViewer(name=self.name)
        self.lv.set_log(self.stderr())


_possible_modules = [
    "aw-server",
    "aw-watcher-afk",
    "aw-watcher-window",
    # "aw-watcher-network"
]

# TODO: Filter away all modules not available on system
modules = {name: Module(name) for name in _possible_modules}


def get_unexpected_stops():
    return list(filter(lambda x: x.started and not x.is_alive(), modules.values()))

if __name__ == "__main__":
    for module in modules.values():
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
