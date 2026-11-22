"""Socketpair harness for HTTP route tests: one request through the real
Handler, no listening socket. Holds no test cases; the test_ prefix keeps it out of the app bundle."""

import json
import socket
from types import SimpleNamespace

import headroom_server


def request(method, path, body=None, peer=("127.0.0.1", 12345), headers=None):
    server_socket, client_socket = socket.socketpair()
    try:
        payload = json.dumps(body).encode() if body is not None else b""
        lines = [
            f"{method} {path} HTTP/1.0",
            "Host: localhost",
        ]
        lines.extend(
            f"{name}: {value}" for name, value in (headers or {}).items()
        )
        if body is not None:
            lines.extend([
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
            ])
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode() + payload
        client_socket.sendall(raw)
        client_socket.shutdown(socket.SHUT_WR)
        headroom_server.Handler(
            server_socket, peer,
            SimpleNamespace(server_port=8737))
        server_socket.close()
        response = b""
        while True:
            chunk = client_socket.recv(65536)
            if not chunk:
                break
            response += chunk
    finally:
        server_socket.close()
        client_socket.close()
    head, payload = response.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(payload)
