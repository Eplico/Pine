# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""A minimal Marionette client (Firefox's built-in remote protocol).

Just enough for Evergreen's smoke tests: new session, chrome context,
execute script, screenshots, quit. Avoids depending on the marionette_driver
package and its dependency tree.

Wire format: each message is `<length>:<json>`. Commands are
[0, id, name, params]; responses are [1, id, error, result].
"""

from __future__ import annotations

import base64
import json
import socket
import time


class MarionetteError(Exception):
    pass


class Marionette:
    def __init__(self, host: str = "127.0.0.1", port: int = 2828, timeout: float = 60):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: socket.socket | None = None
        self._id = 0
        self._buf = b""

    def connect(self, wait: float = 60) -> None:
        deadline = time.time() + wait
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                break
            except OSError:
                if time.time() > deadline:
                    raise MarionetteError(f"Marionette not reachable on {self.host}:{self.port}")
                time.sleep(0.5)
        self._read()  # server hello
        self.command("WebDriver:NewSession", {"capabilities": {}})

    def _read(self):
        while b":" not in self._buf:
            self._recv()
        length, rest = self._buf.split(b":", 1)
        n = int(length)
        while len(rest) < n:
            self._buf = length + b":" + rest
            self._recv()
            length, rest = self._buf.split(b":", 1)
        self._buf = rest[n:]
        return json.loads(rest[:n].decode("utf-8"))

    def _recv(self):
        chunk = self.sock.recv(1 << 16)
        if not chunk:
            raise MarionetteError("Connection closed by Firefox")
        self._buf += chunk

    def command(self, name: str, params: dict | None = None):
        self._id += 1
        payload = json.dumps([0, self._id, name, params or {}]).encode("utf-8")
        self.sock.sendall(str(len(payload)).encode() + b":" + payload)
        while True:
            msg = self._read()
            if isinstance(msg, list) and msg[0] == 1 and msg[1] == self._id:
                _, _, error, result = msg
                if error:
                    raise MarionetteError(f"{name}: {error.get('error')}: {error.get('message')}")
                return result.get("value") if isinstance(result, dict) and "value" in result else result

    def set_context(self, context: str) -> None:
        self.command("Marionette:SetContext", {"value": context})

    def execute(self, script: str, args: list | None = None, timeout_ms: int = 30000):
        """Run `script` (a function body; `arguments` holds args). Promises are awaited."""
        self.command("WebDriver:SetTimeouts", {"script": timeout_ms})
        return self.command("WebDriver:ExecuteScript", {"script": script, "args": args or []})

    def screenshot(self, path: str) -> None:
        data = self.command("WebDriver:TakeScreenshot", {"full": False, "hash": False})
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def quit(self) -> None:
        try:
            self.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
        except (MarionetteError, OSError):
            pass
        finally:
            if self.sock:
                self.sock.close()
                self.sock = None
