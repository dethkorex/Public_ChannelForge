"""Give a Linux relay child the same lifetime as the Studio that starts it."""

import ctypes
import os
import signal
import subprocess
import sys


def main():
    parent = int(sys.argv[1])
    argument = sys.argv[2:]
    if os.getppid() != parent:
        return
    wrapper_pid = os.getpid()
    libc = ctypes.CDLL(None, use_errno=True)

    def attach():
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != wrapper_pid:
            os._exit(1)

    def interrupt(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, interrupt)
    relay = subprocess.Popen(argument, preexec_fn=attach)
    try:
        # The Studio may launch this from an HTTP worker thread. Track the
        # owner's process here, since the worker can safely finish first.
        while os.getppid() == parent:
            try:
                raise SystemExit(relay.wait(timeout=0.5))
            except subprocess.TimeoutExpired:
                pass
    finally:
        if relay.poll() is None:
            relay.terminate()
            try:
                relay.wait(timeout=1)
            except subprocess.TimeoutExpired:
                relay.kill()
                relay.wait()


if __name__ == "__main__":
    main()
