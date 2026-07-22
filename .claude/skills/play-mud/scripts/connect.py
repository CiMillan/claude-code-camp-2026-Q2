#!/usr/bin/env python3
"""Interactive CircleMUD session: relays stdin/stdout to the MUD until EOF.

Credentials come from the environment only — never hardcode them here.
If MUD_NAME/MUD_PASSWORD are unset, drops you at the raw connect banner
without logging in.

Usage:
  python3 connect.py
  MUD_NAME=... MUD_PASSWORD=... python3 connect.py
"""
import os
import selectors
import socket
import sys

import state
from mud_session import ConnectionError, LoginError, MudSession


def main():
    host = os.environ.get("MUD_HOST", "localhost")
    port = int(os.environ.get("MUD_PORT", "4000"))
    timeout = float(os.environ.get("MUD_TIMEOUT", "10"))
    name = os.environ.get("MUD_NAME")
    password = os.environ.get("MUD_PASSWORD")

    session = MudSession(host=host, port=port, timeout=timeout)
    try:
        session.open()
    except ConnectionError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if name and password:
        try:
            print(session.login(name, password, timeout=timeout), end="")
        except LoginError as e:
            print(f"login failed: {e}", file=sys.stderr)
            session.close()
            sys.exit(1)
    else:
        print(session.read_until_quiet(0.5, timeout=timeout), end="")
        print("(MUD_NAME/MUD_PASSWORD not set — showing raw connect banner only)", file=sys.stderr)

    print("--- connected. type commands, Ctrl-D to quit ---", file=sys.stderr)

    session.sock.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(session.sock, selectors.EVENT_READ, "sock")
    sel.register(sys.stdin, selectors.EVENT_READ, "stdin")

    # Best-effort recording: each stdin line is treated as "the command",
    # and every socket chunk that arrives before the *next* line is its
    # output — good enough to feed the same parsers send_command.py uses.
    batch = []
    pending_command = None
    pending_chunks = []

    def flush_pending():
        if pending_command is not None:
            batch.append((pending_command, "".join(pending_chunks)))

    try:
        while True:
            for key, _ in sel.select(timeout=0.2):
                if key.data == "stdin":
                    line = sys.stdin.readline()
                    if not line:
                        return
                    flush_pending()
                    pending_command = line.rstrip("\n")
                    pending_chunks = []
                    session.sock.setblocking(True)
                    session.send_line(pending_command)
                    session.sock.setblocking(False)
                else:
                    try:
                        chunk = session.sock.recv(4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        print("\n[connection closed by server]", file=sys.stderr)
                        return
                    text = session.strip_iac(chunk)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    pending_chunks.append(text)
    except KeyboardInterrupt:
        pass
    finally:
        flush_pending()
        session.close()
        if batch:
            try:
                state.record_batch(batch)
            except Exception as e:
                print(f"warning: state recording failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
