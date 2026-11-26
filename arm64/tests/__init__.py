"""`python3 -m unittest` (from the release directory) runs every tests/test_*.py.

Each of those files is a plain script: it prints `pass` and `FAIL` lines and exits non-zero when anything failed, and it still runs that
way (`python3 tests/test_snapctl.py`). Importing one runs it, so discovery must not import them: this package's load_tests (the hook that
makes unittest hand a package over instead of walking into it) turns each script into one test case that runs it in a child
interpreter and fails with the tail of its output when the exit status is not 0. `python3 -m unittest -v` lists the scripts by name;
a single one: `python3 -m unittest tests.test_snapctl` does NOT work (that would import it), use `python3 tests/test_snapctl.py`.
"""
import glob, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_TIMEOUT = int(os.environ.get("SCRIPT_TIMEOUT", "1800"))


def scripts():
    """the test scripts, sorted: every tests/test_*.py"""
    return sorted(glob.glob(os.path.join(HERE, "test_*.py")))


def _make_case(path):
    def run(self):
        try:
            r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("%s did not finish in %d s (SCRIPT_TIMEOUT)" % (os.path.basename(path), SCRIPT_TIMEOUT))
        out = (r.stdout + r.stderr).splitlines()
        bad = [l for l in out if l.startswith("FAIL")]
        self.assertEqual(r.returncode, 0, "%s exited %d\n%s" % (os.path.basename(path), r.returncode, "\n".join((bad or out)[-25:])))
    return run


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for path in scripts():
        name = os.path.splitext(os.path.basename(path))[0]
        case = type(name, (unittest.TestCase,), {"test_script": _make_case(path)})
        case.__module__ = __name__
        suite.addTest(case("test_script"))
    return suite
