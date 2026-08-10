import socket
s = socket.socket()
s.bind(("127.0.0.1", 8085))
s.listen(1)
conn, addr = s.accept()
print(conn.recv(4096).decode("utf-8"))
conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
conn.close()
