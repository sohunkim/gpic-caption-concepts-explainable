from __future__ import annotations

from pathlib import Path


FORBIDDEN_REMOTE_SHELL_MARKERS = (
    "\n",
    "\r",
    ";",
    "&&",
    "||",
    "|",
    "`",
    "$(",
)


def reject_ad_hoc_multistep_ssh(command: list[str]) -> None:
    if not command:
        return
    executable = Path(command[0]).name.lower()
    if executable not in {"ssh", "ssh.exe"}:
        return
    unsafe = [
        argument
        for argument in command[1:]
        if any(marker in argument for marker in FORBIDDEN_REMOTE_SHELL_MARKERS)
    ]
    if unsafe:
        raise SystemExit(
            "Refusing an ad-hoc multi-step SSH shell command. Put the remote "
            "commands in a checked .sh file, upload it with scp, and invoke "
            "that script with one simple SSH command."
        )
