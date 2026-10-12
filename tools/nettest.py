"""Measure the throughput of the exact path the video uses: adb reverse -> TCP.

The phone runs `toybox nc` as a client that connects back to the host through
the reverse tunnel and just swallows bytes, so this isolates USB/adbd throughput
from MediaCodec, the filesystem and our own protocol.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, socket, subprocess, sys, threading, time

ROOT = REPO
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SERIAL = sys.argv[1] if len(sys.argv) > 1 else "d1de99c4"
PORT = 27199

rc = subprocess.run([ADB, "-s", SERIAL, "shell", "toybox", "nc", "--help"],
                    capture_output=True, text=True)
print("nc present:", "usage" in (rc.stdout + rc.stderr).lower() or rc.returncode == 0)
print((rc.stdout + rc.stderr)[:200])

subprocess.run([ADB, "-s", SERIAL, "reverse", "tcp:%d" % PORT, "tcp:%d" % PORT],
               capture_output=True)
print("reverse ok")

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(1)
srv.settimeout(20)

proc = subprocess.Popen(
    [ADB, "-s", SERIAL, "shell", "toybox nc 127.0.0.1 %d > /dev/null" % PORT],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    conn, addr = srv.accept()
except socket.timeout:
    print("phone never connected")
    proc.kill()
    sys.exit(1)
print("phone connected from", addr)
conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)

chunk = b"\x5a" * 65536
start = time.time()
sent = 0
for _ in range(240):          # up to 15 MB, but stop after 10s
    try:
        conn.sendall(chunk)
    except OSError as e:
        print("send failed:", e)
        break
    sent += len(chunk)
    el = time.time() - start
    if el > 10:
        break
el = time.time() - start
print("sent %.2f MB in %.2fs => %.2f MB/s = %.1f Mbps" %
      (sent / 1048576.0, el, sent / 1048576.0 / el, sent * 8 / 1048576.0 / el))

conn.close()
srv.close()
proc.kill()
subprocess.run([ADB, "-s", SERIAL, "reverse", "--remove", "tcp:%d" % PORT],
               capture_output=True)
print("done")
