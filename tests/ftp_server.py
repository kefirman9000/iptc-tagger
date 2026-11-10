"""A small FTP/FTPS server that runs inside the tests (from StockUp's tests). Accepts user u, password pw."""
import os
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import time


def make_cert(folder):
    """Self-signed certificate for the test server, or None if openssl isn't installed."""
    if not shutil.which("openssl"):
        return None
    key, cert = os.path.join(folder, "key.pem"), os.path.join(folder, "cert.pem")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", cert, "-days", "2", "-subj", "/CN=localhost"],
                       capture_output=True)
    return (cert, key) if r.returncode == 0 else None


class FTPTestServer:
    """Accepts user 'u' with password 'pw'. Options: tls, slow (seconds per 64 KB received),
    drop_once (file names whose first upload is cut off part-way)."""

    def __init__(self, root, cert=None, slow=0.0, drop_once=()):
        self.root, self.cert, self.slow = root, cert, slow
        self.drop_once = set(drop_once)
        self.stors = []  # names of every STOR received, in order
        ctx = None
        if cert:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*cert)
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def send(self, line):
                self.wfile.write((line + "\r\n").encode())
                self.wfile.flush()

            def handle(self):
                prot, pasv = False, None
                self.send("220 test server")
                while True:
                    try:
                        line = self.rfile.readline()
                    except (OSError, ssl.SSLError):
                        return
                    if not line:
                        return
                    cmd, _, arg = line.decode().strip().partition(" ")
                    cmd = cmd.upper()
                    if cmd == "AUTH":
                        if not ctx:
                            self.send("502 no TLS here")
                            continue
                        self.send("234 go ahead")
                        s = ctx.wrap_socket(self.request, server_side=True)
                        self.request, self.rfile, self.wfile = s, s.makefile("rb"), s.makefile("wb")
                    elif cmd == "USER":
                        self.send("331 password please")
                    elif cmd == "PASS":
                        self.send("230 welcome" if arg == "pw" else "530 Login incorrect")
                    elif cmd in ("PBSZ", "TYPE", "CWD"):
                        self.send("200 ok")
                    elif cmd == "PROT":
                        prot = arg == "P"
                        self.send("200 ok")
                    elif cmd == "PASV":
                        pasv = socket.socket()
                        # a small receive window, so uploads go at the server's pace like a real
                        # internet connection, instead of vanishing into a big local buffer
                        pasv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
                        pasv.bind(("127.0.0.1", 0))
                        pasv.listen(1)
                        p = pasv.getsockname()[1]
                        self.send("227 Entering Passive Mode (127,0,0,1,%d,%d)" % (p // 256, p % 256))
                    elif cmd in ("NLST", "STOR"):
                        c, _ = pasv.accept()
                        pasv.close()
                        self.send("150 opening data connection")
                        if prot:
                            c = ctx.wrap_socket(c, server_side=True)
                        try:
                            if cmd == "NLST":
                                c.sendall("".join(n + "\r\n" for n in sorted(os.listdir(server.root))).encode())
                            else:
                                server.stors.append(arg)
                                got = 0
                                with open(os.path.join(server.root, arg), "wb") as fh:
                                    while True:
                                        d = c.recv(65536)
                                        if not d:
                                            break
                                        fh.write(d)
                                        got += len(d)
                                        if server.slow:
                                            time.sleep(server.slow)
                                        if arg in server.drop_once and got > 200_000:
                                            server.drop_once.discard(arg)
                                            c.close()
                                            self.request.close()
                                            return
                            if prot:
                                try:
                                    c.unwrap()
                                except (OSError, ssl.SSLError):
                                    pass
                        except (OSError, ssl.SSLError):
                            c.close()
                            continue
                        c.close()
                        self.send("226 transfer complete")
                    elif cmd == "SIZE":
                        p = os.path.join(server.root, arg)
                        self.send("213 %d" % os.path.getsize(p) if os.path.exists(p) else "550 no such file")
                    elif cmd == "DELE":
                        p = os.path.join(server.root, arg)
                        if os.path.exists(p):
                            os.remove(p)
                            self.send("250 deleted")
                        else:
                            self.send("550 no such file")
                    elif cmd == "QUIT":
                        self.send("221 bye")
                        return
                    else:
                        self.send("502 not implemented")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def site(self, name, protocol="ftp", password="pw"):
        return {"name": name, "enabled": True, "protocol": protocol, "host": "127.0.0.1",
                "port": self.port, "username": "u", "password": password, "remote_dir": "/"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()

