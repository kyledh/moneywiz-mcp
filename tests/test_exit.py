"""How the server ends when an MCP client stops it (a real server process, no MoneyWiz involved)."""
import json
import os
import signal
import subprocess
import sys
import threading

import pytest


@pytest.fixture
def server(tmp_path):
    """A running server with MoneyWiz looked after (MONEYWIZ_LAUNCH=1) and its lease in a scratch
    home, past the MCP handshake: its stdio reader is then waiting in read() on the input."""
    env = {**os.environ, "HOME": str(tmp_path), "MONEYWIZ_LAUNCH": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "moneywiz_mcp.server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    watchdog = threading.Timer(60, proc.kill)  # a server that never answers fails the test, not the run
    watchdog.start()
    try:
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}}
        proc.stdin.write((json.dumps(hello) + "\n").encode())
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 1
        yield proc
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_sigterm_ends_the_server_while_its_input_is_open(server):
    # Clients may send SIGTERM without closing the server's input. The stdio reader then stays
    # blocked in read(), and the interpreter's normal exit waits for that thread (before running
    # atexit) forever: up to 0.2.0 the server stopped answering but never exited.
    server.send_signal(signal.SIGTERM)
    assert server.wait(timeout=20) == 0


def test_closing_the_input_ends_the_server(server):
    server.stdin.close()
    assert server.wait(timeout=20) == 0
