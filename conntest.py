#!/usr/bin/env python3

import socket

SERVER_IP = '192.168.50.183'
SERVER_PORT = 8560

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex((SERVER_IP, SERVER_PORT))
if result != 0:
    print(f"Server is not running on {SERVER_IP}:{SERVER_PORT}")
    exit()

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
try:
    sock.connect((SERVER_IP, SERVER_PORT))
except socket.error as e:
    print(f"Server is not listening on {SERVER_IP}:{SERVER_PORT}")
    exit()

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
try:
    sock.connect((SERVER_IP, SERVER_PORT))
except socket.error as e:
    print(f"Connection refused: {e}")
    exit()