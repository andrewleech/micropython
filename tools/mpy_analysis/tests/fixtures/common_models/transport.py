import socket


def read_payload(connection: socket.socket) -> bytes:
    return connection.recv(128)


def read_datagram(connection: socket.socket) -> bytes:
    payload, _ = connection.recvfrom(128)
    return payload


def execute_payload(payload: bytes):
    return eval(payload)


def literal_expression(untrusted: str) -> str:
    if untrusted == "status":
        return "1 + 1"
    return "0"
