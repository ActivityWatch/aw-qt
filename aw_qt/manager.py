import os
from time import sleep
import logging
from contextlib import closing

import asyncio
from asyncio import subprocess
from asyncio.subprocess import PIPE

from .gui.logviewer import LogViewer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aw.qt.manager")


class _Module:
    """
    Manages a subprocess using asyncio

    https://stackoverflow.com/posts/20697159/revisions
    """

    def __init__(self, name):
        self.name = name
        self.started = False
        self._process = None
        self._last_process = None
        self._log = ""

    def start(self, testing=False):
        async def _start():
            logger.info("Starting module {}".format(self.name))

            # Will start module from localdir if present there,
            # otherwise will try to call what is available in PATH.
            exec_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), self.name)
            if os.path.isfile(exec_path):
                # Executable for module was found in same dir as aw-qt, use it
                cmd = exec_path
            else:
                # Executable for module was not in same dir as aw-qt, use executable in PATH
                cmd = self.name

            # Args to executable
            args = " --testing" if testing else ""

            # Start the process
            exec_cmd = " ".join([cmd, args]).strip()
            self._process = await asyncio.create_subprocess_exec(exec_cmd, universal_newlines=True,
                                                                 stdout=PIPE, stderr=PIPE)
            self.started = True

        loop.call_soon_threadsafe(_start)

    def stop(self):
        # Stops a module, and waits asynchronously until it terminates.
        async def _stop():
            # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
            if not self.started:
                logger.warning("Tried to kill module {}, but it has never been started".format(self.name))
            elif not self.is_alive():
                logger.warning("Tried to kill module {}, but it was already dead".format(self.name))
            else:
                logger.info("Stopping module {}".format(self.name))
                self._process.terminate()
                logger.info("Waiting for module {} to shut down".format(self.name))
                await self._process.wait()
                logger.info("Module {} has shut down".format(self.name))
            assert not self.is_alive()
            self._last_process = self._process
            self._process = None
            self.started = False

        loop.call_soon_threadsafe(_stop)

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
        if self._process:
            print("Reading active process stderr...")
            log = self._process.stderr.read()
            self._log += log
        print("Read stderr")
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
modules = {name: _Module(name) for name in _possible_modules}


def get_unexpected_stops():
    return list(filter(lambda x: x.started and not x.is_alive(), modules.values()))


@blocking
def run():
    # NOTE: To handle signals and to execute subprocesses, the event loop must be run in the main thread.
    # https://docs.python.org/3/library/asyncio-dev.html#concurrency-and-multithreading
    assert threading.current_thread() == threading.main_thread()

    global loop
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()

    with closing(loop):
        loop.run_forever()
    logging.error("Event loop stopped, something must have went wrong")


def start_module(name):
    modules[name].start()


def stop_module(name):
    modules[name].stop()


if __name__ == "__main__":
    for module in modules.values():
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
