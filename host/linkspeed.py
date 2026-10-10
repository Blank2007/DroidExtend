"""Measure how fast bytes actually travel down the USB cable to the phone.

The video goes  host -> adbd -> USB -> phone,  and on the phone it lands on the
loopback socket that `adb reverse` created.  So a plain TCP client on the phone
that connects to that same loopback port and throws the bytes away measures the
*identical* path, without MediaCodec or our own protocol in the way.

Why this matters: a surprising number of cables are charge-only wiring and only
negotiate USB 1.1 Full Speed, which caps the link at ~12 Mbps no matter what the
port supports.  Encoding above that just produces frames the phone never gets.
"""
import socket
import subprocess
import time

from paths import adb_exe, popen

PROBE_PORT = 27199

# Safety valve only.  The measurement must NOT stop early on a byte count: the
# first ~40 MB vanish into socket/adbd buffers at memory speed, and cutting the
# window off there reports that buffer fill as link speed (we saw a fake
# 589 Mbps on a 140 Mbps link).  Letting the loop run into real backpressure and
# counting the bytes the phone confirms over the *whole* send+drain span cancels
# the buffering out exactly, because buffered bytes still have to cross the wire
# and that time shows up in the drain.
MAX_SEND = 256 << 20
# How long to wait for the phone to finish draining and report its count.
DRAIN_LIMIT = 60.0


def _count_received(proc, timeout):
    """Read the byte count the phone wires back, or None if it never came."""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:
            return None
    txt = (out or b"").decode("utf-8", "replace").strip().split()
    for tok in reversed(txt):
        if tok.isdigit():
            return int(tok)
    return None


def measure(adb, serial, seconds=6.0, port=PROBE_PORT, log=None):
    """Return {'mbps','mb_per_s','bytes','seconds','error'} for the USB link."""
    say = log or (lambda m, t=None: None)
    out = {"mbps": 0.0, "mb_per_s": 0.0, "bytes": 0, "seconds": 0.0, "error": None,
           "sent": 0, "verified": False}

    if adb is None:
        out["error"] = "adb 不可用"
        return out

    rc, txt = adb.run("-s", serial, "shell", "toybox", "nc", "--help", timeout=15)
    if rc != 0 and "usage" not in (txt or "").lower():
        out["error"] = "手机上没有 toybox nc，无法测速"
        return out

    adb.reverse(serial, port, port)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    srv.settimeout(20)

    proc = None
    conn = None
    try:
        # Two things this command line has to get right:
        #  * stdin MUST be a pipe we keep open -- `toybox nc` quits the instant
        #    its stdin reaches EOF, and adbd then swallows our bytes into its own
        #    buffers, which measures memory bandwidth rather than USB (we saw an
        #    absurd 2862 Mbps that way).
        #  * `| wc -c` makes the *phone* report how much it actually received.
        #    Counting only what sendall() accepted is not a measurement: if the
        #    far end stalls, adbd buffers and sendall() never blocks, so a dead
        #    tunnel scores as infinitely fast (observed: 556 Mbps / 417 MB on a
        #    12 Mbps cable).
        proc = popen([adb.exe, "-s", serial, "shell",
                      "toybox nc 127.0.0.1 %d | wc -c" % port],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL)
        # the phone dials us through the reverse tunnel
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            out["error"] = "手机没有回连（reverse 隧道不通）"
            return out

        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        except OSError:
            pass

        chunk = b"\x5a" * 65536
        sent = 0
        start = time.time()
        while time.time() - start < seconds and sent < MAX_SEND:
            try:
                conn.sendall(chunk)
            except OSError as e:
                out["error"] = "链路中断: %s" % e
                break
            sent += len(chunk)
        out["sent"] = sent

        # Half-close so the phone's nc sees EOF on its socket and stops, which
        # lets `wc -c` print and adb exit.
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        recv = _count_received(proc, DRAIN_LIMIT)
        el = max(time.time() - start, 1e-6)

        if not recv:
            out["error"] = ("手机没有回报收到多少字节（链路拥塞或隧道中断），"
                            "本次测速不可信")
            say("测速失败: 发出 %.1f MB，手机未回报接收量" % (sent / 1048576.0), "err")
            return out

        out["verified"] = True
        out["bytes"] = recv
        out["seconds"] = el
        out["mb_per_s"] = recv / 1048576.0 / el
        out["mbps"] = recv * 8 / 1048576.0 / el
        say("链路实测: %.2f MB/s = %.1f Mbps（手机收到 %.1f MB / %.1fs）" % (
            out["mb_per_s"], out["mbps"], recv / 1048576.0, el), "ok")
        return out
    finally:
        for c in (conn, srv):
            try:
                c.close()
            except Exception:
                pass
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            adb.run("-s", serial, "reverse", "--remove", "tcp:%d" % port, timeout=15)
        except Exception:
            pass


def verdict(mbps):
    """Human hint about what a measured link can carry."""
    if mbps <= 0:
        return "未知"
    if mbps < 15:
        return "USB 1.1 全速线缆（换一根数据线可大幅提速）"
    if mbps < 60:
        return "USB 1.1/2.0 全速（偏低，换线或换口更好）"
    if mbps < 400:
        return "USB 2.0 高速"
    return "USB 3.x 级别"
