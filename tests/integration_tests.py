import subprocess
from subprocess import PIPE
from time import sleep
import sys
from contextlib import contextmanager
from typing import List

import sys

# NOTE:
#   This entire file contains a lot of duplicate/similar code
#   from the aw-server/aw-client integration test. We might want
#   to deduplicate at some point, especially if we want to write
#   integration tests like this ones for other components.

# TODO: Write a context manager for the server process
@contextmanager
def running_process(call: List[str]):
    proc = subprocess.Popen(call, stdout=PIPE, stderr=PIPE)
    yield proc
    proc.kill()


def print_section(msg, title="unnamed section"):
    start_line = "=" * 10 + " " + title + " " + "=" * 10
    print(start_line)
    print(msg)
    print("=" * len(start_line))


if __name__ == "__main__":
    with running_process(["aw-qt", "--testing", "--autostart-modules=none"]) as proc:
        # If it stays alive this long, things are probably fine
        sleep(5)

    exit_code = proc.poll()
    out, err = proc.communicate()
    out, err = (str(stream, "utf8") for stream in (out, err))
    if out:
        print_section(out, title="stdout")
    if err:
        print_section(err, title="stderr")

    sys.exit(exit_code)
