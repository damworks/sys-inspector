"""Local port forwarding over an already open Paramiko connection (direct-tcpip)."""
from __future__ import annotations

import select
import socketserver
import threading

import paramiko


class TunnelError(Exception):
    pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        srv = self.server
        try:
            chan = srv.transport.open_channel(
                "direct-tcpip",
                (srv.remote_host, srv.remote_port),
                self.request.getpeername(),
            )
        except (paramiko.SSHException, OSError):
            return
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [], 1.0)
                if self.request in r:
                    data = self.request.recv(32768)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(32768)
                    if not data:
                        break
                    self.request.sendall(data)
        except OSError:
            pass
        finally:
            chan.close()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class SSHTunnel:
    """Forwards 127.0.0.1:<ephemeral local port> -> remote_host:remote_port via SSH."""

    def __init__(self, transport: paramiko.Transport, remote_host: str, remote_port: int):
        if transport is None or not transport.is_active():
            raise TunnelError("SSH transport is not active")
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.transport = transport
        self._server.remote_host = remote_host
        self._server.remote_port = int(remote_port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def local_port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> "SSHTunnel":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
