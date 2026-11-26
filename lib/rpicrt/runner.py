"""External command execution.

On the live system commands come from the standard system PATH. On a staged
root (tests) commands are resolved *only* from ``RPI_CRT_MOCK_BIN`` so that a
test can never rebuild the developer's initramfs or install packages.
"""

import os
import shutil
import subprocess

SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class RunnerError(Exception):
    pass


class Result:
    def __init__(self, argv, returncode, stdout, stderr):
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self):
        return self.returncode == 0


class Runner:
    def __init__(self, root):
        self.root = root
        if root.is_live:
            self.path = SYSTEM_PATH
        else:
            mock = os.environ.get("RPI_CRT_MOCK_BIN")
            if not mock or not os.path.isdir(mock):
                raise RunnerError(
                    "a staged --root requires RPI_CRT_MOCK_BIN to point at a directory of mock "
                    "commands; refusing to run real system commands against a staged root"
                )
            self.path = os.path.abspath(mock)

    def which(self, name):
        return shutil.which(name, path=self.path)

    def _env(self, extra=None):
        env = {"PATH": self.path, "LC_ALL": "C", "LANG": "C"}
        for key in ("HOME",):
            if key in os.environ:
                env[key] = os.environ[key]
        if not self.root.is_live:
            env["RPI_CRT_ROOT"] = self.root.root
            for key, value in os.environ.items():
                if key.startswith("RPI_CRT_"):
                    env.setdefault(key, value)
        if extra:
            env.update(extra)
        return env

    def run(self, argv, check=False, timeout=600, env=None, input_text=None):
        exe = self.which(argv[0])
        if exe is None:
            if check:
                raise RunnerError("required command not found: %s" % argv[0])
            return Result(argv, 127, "", "%s: command not found" % argv[0])
        try:
            proc = subprocess.run(
                [exe] + list(argv[1:]),
                env=self._env(env),
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if check:
                raise RunnerError("command timed out: %s" % " ".join(argv))
            return Result(argv, 124, "", "timed out after %ss" % timeout)
        result = Result(argv, proc.returncode, proc.stdout, proc.stderr)
        if check and not result.ok:
            raise RunnerError(
                "command failed (%d): %s\n%s" % (result.returncode, " ".join(argv), (result.stderr or result.stdout).strip())
            )
        return result
