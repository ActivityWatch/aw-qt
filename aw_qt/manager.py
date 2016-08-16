from time import sleep
import logging
import subprocess
from subprocess import PIPE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aw.qt.manager")


class Module:
    def __init__(self, name):
        self.name = name
        self.process = None

    def start(self):
        logger.info("Starting module {}".format(self.name))
        self.process = subprocess.Popen(self.name, universal_newlines=True,
                                        stdout=PIPE, stderr=PIPE)

    def stop(self):
        """
        Stops a module, and waits until it terminates.
        """
        # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
        if not self.is_running():
            logger.warning("Tried to kill module {}, but it was already dead".format(self.name))
            return
        logger.info("Stopping module {}".format(self.name))
        self.process.terminate()
        logger.info("Waiting for module {} to shut down".format(self.name))
        self.process.wait()
        logger.info("Module {} has shut down".format(self.name))

    def is_running(self):
        if not self.process:
            return False

        self.process.poll()
        # If returncode is none after p.poll(), module is still running
        return True if self.process.returncode is None else False

    def stderr(self):
        """Useful if you want to retrieve logs written to stderr"""
        return self.process.stderr.read()


modules = [Module(name) for name in ["aw-server", "aw-watcher-afk", "aw-watcher-x11"]]

if __name__ == "__main__":
    for module in modules:
        module.start()
        sleep(2)
        module.stop()
