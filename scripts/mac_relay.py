"""macOS dev helper only. Homebrew/uv Pythons can be blocked by macOS Local Network
privacy, while /usr/bin/python3 is allowed. Run this with /usr/bin/python3 to relay
127.0.0.1 ports to LAN devices:  /usr/bin/python3 scripts/mac_relay.py 18080=NVR_IP:80 18123=HA_IP:80
Not needed on Windows or Linux."""
import socket, sys, threading

def pipe(a, b):
    try:
        while (d := a.recv(65536)):
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def serve(lport, host, port):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", lport)); srv.listen(32)
    while True:
        c, _ = srv.accept()
        try: r = socket.create_connection((host, port), timeout=10); r.settimeout(None)
        except OSError: c.close(); continue
        threading.Thread(target=pipe, args=(c, r), daemon=True).start()
        threading.Thread(target=pipe, args=(r, c), daemon=True).start()

for spec in sys.argv[1:]:
    lp, dest = spec.split("="); h, p = dest.rsplit(":", 1)
    threading.Thread(target=serve, args=(int(lp), h, int(p)), daemon=True).start()
threading.Event().wait()
