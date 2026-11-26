"""pdeath.py CMD ARGS...: run CMD so that it dies the moment its parent dies (PR_SET_PDEATHSIG, SIGKILL), then exec it.
QEMU runs under `timeout`; when that wrapper was SIGKILLed QEMU lived on (ppid 1) with the pack lock and the ssh port.
With this in between, `timeout` is the parent of QEMU and its death takes QEMU with it. The exec keeps the setting (it is only cleared
for set-uid programs). Run it as `python3 -I pdeath.py`."""
import ctypes, os, signal, sys

PR_SET_PDEATHSIG = 1
if len(sys.argv) < 2:
    sys.exit("usage: pdeath.py CMD [ARGS...]")
ppid = os.getppid()
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
    sys.exit("pdeath: prctl failed: %s" % os.strerror(ctypes.get_errno()))
if os.getppid() != ppid:   # the parent died before the setting took effect: nothing to guard any more
    os._exit(137)
try:
    os.execvp(sys.argv[1], sys.argv[1:])
except OSError as e:
    sys.exit("pdeath: cannot run %s: %s" % (sys.argv[1], e))
