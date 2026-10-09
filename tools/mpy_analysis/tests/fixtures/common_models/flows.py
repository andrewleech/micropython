import socket

from transport import execute_payload, literal_expression, read_datagram, read_payload


def positive_input_eval():
    return eval(input())


def positive_input_exec():
    exec(input("code: "))


def positive_cross_module(connection: socket.socket):
    return execute_payload(read_payload(connection))


def positive_datagram(connection: socket.socket):
    return execute_payload(read_datagram(connection))


def negative_constant():
    input()
    return eval("1 + 1")


def negative_received_but_unused(connection: socket.socket):
    read_payload(connection)
    return execute_payload(b"1 + 1")


def negative_literal_selection():
    return eval(literal_expression(input()))
