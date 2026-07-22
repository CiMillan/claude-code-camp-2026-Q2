"""Minimal telnet session to a CircleMUD-compatible server.

Handles IAC negotiation stripping and the CircleMUD login dance. No external
dependencies — socket and re are stdlib.
"""
import re
import socket
import time

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

NAME_PROMPT = re.compile(r"name.*\?", re.I)
PASSWORD_PROMPT = re.compile(r"password", re.I)
LOGIN_RESULT = re.compile(r"welcome|reconnecting|wrong password", re.I)


class ConnectionError(Exception):
    pass


class LoginError(Exception):
    pass


class MudSession:
    def __init__(self, host="localhost", port=4000, timeout=10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.buffer = ""

    def open(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ConnectionError(f"connect {self.host}:{self.port} failed: {e}")
        self.sock.settimeout(self.timeout)
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def strip_iac(self, data: bytes) -> str:
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            b = data[i]
            if b == IAC:
                nxt = data[i + 1] if i + 1 < n else None
                if nxt is None:
                    break
                if nxt == IAC:
                    out.append(0xFF)
                    i += 2
                elif nxt in (WILL, WONT, DO, DONT):
                    i += 3
                elif nxt == SB:
                    j = i + 2
                    while j < n - 1 and not (data[j] == IAC and data[j + 1] == SE):
                        j += 1
                    i = j + 2
                else:
                    i += 2
            else:
                out.append(b)
                i += 1
        return out.decode("utf-8", errors="replace")

    def _pump(self):
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return False
        if not chunk:
            raise ConnectionError("socket closed by remote")
        self.buffer += self.strip_iac(chunk)
        return True

    def read_until(self, pattern, timeout=None):
        regexp = pattern if isinstance(pattern, re.Pattern) else re.compile(re.escape(pattern))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            m = regexp.search(self.buffer)
            if m:
                out = self.buffer[: m.end()]
                self.buffer = self.buffer[m.end():]
                return out
            if time.monotonic() >= deadline:
                raise TimeoutError(f"read_until {pattern!r} timed out")
            self._pump()

    def read_until_quiet(self, quiet_seconds=0.5, timeout=None):
        deadline = time.monotonic() + (timeout or self.timeout)
        last_recv = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            got = self._pump()
            now = time.monotonic()
            if got:
                last_recv = now
            if last_recv and (now - last_recv) >= quiet_seconds and self.buffer:
                break
        out, self.buffer = self.buffer, ""
        return out

    def send_line(self, text: str):
        self.sock.sendall((text + "\r\n").encode("utf-8"))

    def login(self, name, password, timeout=None):
        self.read_until(NAME_PROMPT, timeout=timeout)
        self.send_line(name)
        self.read_until(PASSWORD_PROMPT, timeout=timeout)
        self.send_line(password)
        out = self.read_until(LOGIN_RESULT, timeout=timeout)
        if re.search(r"wrong password", out, re.I):
            raise LoginError("wrong password")
        if re.search(r"welcome", out, re.I):
            self.send_line("")   # enter for main menu
            self.send_line("1")  # enter the game
            out += self.read_until_quiet(0.5, timeout=timeout)
        return out
