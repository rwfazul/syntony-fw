"""Run local commands and execute argument arrays on Grid nodes through SSH."""

import shlex
import subprocess
import sys


def command(args, **kwargs):
    """Run a local argument array and report captured output when it fails."""
    try:
        return subprocess.run(args, check=True, text=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if kwargs.get("capture_output"):
            if exc.stdout:
                print(exc.stdout, file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        raise


def ssh(host, args, capture=False, timeout=None):
    """Execute a quoted command on a node and optionally return its stdout."""
    return command(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@" + host, shlex.join(args)],
        capture_output=capture,
        timeout=timeout,
    ).stdout
