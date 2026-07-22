#!/usr/bin/env python3
"""One-shot MUD command runner: connect, log in, send command(s), print output, disconnect.

Credentials come from the environment only — never hardcode them here.

Usage:
  MUD_NAME=... MUD_PASSWORD=... python3 send_command.py look
  MUD_NAME=... MUD_PASSWORD=... python3 send_command.py look score inventory
"""
import os
import sys

import state
from mud_session import ConnectionError, LoginError, MudSession


def main():
    if len(sys.argv) < 2:
        print("usage: send_command.py <command> [command2 ...]", file=sys.stderr)
        sys.exit(2)

    host = os.environ.get("MUD_HOST", "localhost")
    port = int(os.environ.get("MUD_PORT", "4000"))
    timeout = float(os.environ.get("MUD_TIMEOUT", "10"))
    name = os.environ.get("MUD_NAME")
    password = os.environ.get("MUD_PASSWORD")

    if not name or not password:
        print("error: set MUD_NAME and MUD_PASSWORD in the environment", file=sys.stderr)
        sys.exit(2)

    session = MudSession(host=host, port=port, timeout=timeout)
    batch = []
    try:
        session.open()
        session.login(name, password, timeout=timeout)
        for command in sys.argv[1:]:
            session.send_line(command)
            output = session.read_until_quiet(0.5, timeout=timeout)
            print(output, end="")
            batch.append((command, output))
    except (ConnectionError, LoginError, TimeoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()
        if batch:
            try:
                state.record_batch(batch)
            except Exception as e:
                print(f"warning: state recording failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
