#!/usr/bin/env python3
"""Run an ssh/scp command that prompts for a password through a real PTY.

This helper is intentionally Linux/WSL-only.  Password-based OpenSSH clients
expect a controlling terminal; using ``pty.openpty()`` with ``subprocess`` is
not enough in some environments and can make a correct password look wrong.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pty
import select
import signal
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from incident_gate import guarded_entrypoint
from ssh_command_policy import reject_ad_hoc_multistep_ssh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a password-prompting ssh/scp command through pty.fork()."
    )
    parser.add_argument(
        "--password-env",
        default="SSH_PASSWORD",
        help="Environment variable that contains the password.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=0.0,
        help="Hard timeout for the child process. 0 disables the timeout.",
    )
    parser.add_argument(
        "--prompt-timeout-seconds",
        type=float,
        default=90.0,
        help="Fail if no password prompt appears within this many seconds.",
    )
    parser.add_argument(
        "--",
        dest="separator",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")
    return args


def exit_code_from_wait_status(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def main() -> int:
    args = parse_args()
    reject_ad_hoc_multistep_ssh(args.command)
    if not hasattr(pty, "fork"):
        print("ERROR: pty.fork() is unavailable; run this helper under Linux/WSL.", file=sys.stderr)
        return 2

    password = os.environ.get(args.password_env)
    if not password:
        print(f"ERROR: environment variable {args.password_env!r} is empty.", file=sys.stderr)
        return 2

    start = time.monotonic()
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(args.command[0], args.command)

    sent_password = False
    buffer = ""
    child_status: int | None = None

    try:
        while True:
            if args.timeout_seconds and time.monotonic() - start > args.timeout_seconds:
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                print("\nERROR: child process timed out.", file=sys.stderr)
                return 124

            if (
                not sent_password
                and args.prompt_timeout_seconds
                and time.monotonic() - start > args.prompt_timeout_seconds
            ):
                os.kill(pid, signal.SIGTERM)
                print("\nERROR: password prompt did not appear before timeout.", file=sys.stderr)
                return 124

            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    text = chunk.decode(errors="replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    buffer = (buffer + text)[-4096:]
                    if not sent_password and "password:" in buffer.lower():
                        os.write(fd, (password + "\n").encode())
                        sent_password = True
                else:
                    _, child_status = os.waitpid(pid, 0)
                    break

            ended_pid, status = os.waitpid(pid, os.WNOHANG)
            if ended_pid == pid:
                child_status = status
                break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if child_status is None:
        return 1
    return exit_code_from_wait_status(child_status)


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("password_ssh_or_scp", main))
