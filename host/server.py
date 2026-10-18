"""The wire protocol between the Windows host and the phone.

    magic   b"DXS1"                    4
    width   uint32 little endian       4
    height  uint32 little endian       4
    fps     uint32 little endian       4
    ------------------------------------
    then, for every access unit:
    size    uint32 little endian       4
    payload size bytes (Annex-B)

The phone reaches us on 127.0.0.1:<port> through `adb reverse`, so the whole
thing rides the USB cable and never touches the network stack.
"""
import socket
import struct
import threading
import time

from clock import now_us

MAGIC = b"DXS3"
HOST_PORT = 27183
MAX_UNIT = 8 * 1024 * 1024


def _now_us():
    """The frame/​handshake timestamp clock.  Monotonic, see clock.py.

    Kept as a name so the tests can keep referring to it; it used to be
    `time.time()` in microseconds, which is the wall clock -- and a wall clock
    that gets stepped by the time service turns into "the latency jumped" on the
    phone.
    """
    return now_us()


def header(width, height, fps, note=""):
    """DXS3 = DXS2 plus a free-form description of the current picture quality.

    DXS2 added a host timestamp so the phone can report the real end-to-end
    latency instead of just decoding blindly.  DXS3 appends the encoder, rate
    control and quality the host is actually using, so the phone's overlay can
    say what 画质 you are looking at instead of guessing from the bitrate.
    The note is length-prefixed UTF-8, so a phone that does not know about it
    reads exactly the bytes it expects and stops.
    """
    raw = (note or "").encode("utf-8")[:255]
    return struct.pack("<4sIIIQ", MAGIC, int(width), int(height), int(fps),
                       _now_us()) + struct.pack("<B", len(raw)) + raw


class StreamServer(object):
    """Accepts one phone at a time and pushes access units at it.

    A phone that drops off (app restarted, cable bumped) can reconnect and gets
    a fresh handshake, so the app's retry loop always has something to land on.

    Slow-link policy: the capture thread keeps the newest frames, and if the
    socket cannot keep up we throw away non-keyframe units instead of growing
    latency without bound.  Keyframes are always sent, so the phone can always
    resynchronise.
    """

    def __init__(self, port=HOST_PORT, log=None, on_client=None):
        self.port = port
        self.log = log or (lambda m: None)
        self.on_client = on_client or (lambda s: None)
        self.sock = None
        self.conn = None
        self.addr = None
        self._stop = threading.Event()
        self._accept_thread = None
        self._send_thread = None
        self._cv = threading.Condition()
        self._queue = []
        self._queue_bytes = 0
        self.max_queue_bytes = 4 * 1024 * 1024
        self.stats = {"sent": 0, "dropped": 0, "bytes": 0, "clients": 0}

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(1)
        self.sock = s
        self._accept_thread = threading.Thread(target=self._accept, name="accept", daemon=True)
        self._accept_thread.start()
        self.log("监听 127.0.0.1:%d，等待手机连接…" % self.port)

    def _accept(self):
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # A phone that reconnects (app restarted, cable replugged, transfer
            # stalled) must be able to get back in.  The old accept loop
            # returned after the first client, so a relaunched app completed the
            # TCP handshake from the listen backlog and then sat on "connecting"
            # forever because nobody ever accepted it.
            old = self.conn
            self._drop_client(quiet=True)
            if old is not None:
                self.log("上一路连接已断开，接受新连接")
                with self._cv:
                    self._queue = []
                    self._queue_bytes = 0
            self.conn = conn
            self.addr = addr
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            self.stats["clients"] += 1
            self.log("手机已连接: %s:%d" % addr)
            self._send_thread = threading.Thread(target=self._send, args=(conn,),
                                                 name="send", daemon=True)
            self._send_thread.start()
            with self._cv:
                self._cv.notify_all()
            self.on_client(addr)
        self.log("监听线程退出")

    def _drop_client(self, quiet=False):
        conn, self.conn = self.conn, None
        if conn is None:
            return
        if not quiet:
            self.log("断开手机连接")
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        with self._cv:
            self._cv.notify_all()

    def drop_client(self):
        """Kick the current phone off on purpose.

        Used when the picture size changes mid-session (the user picked a new
        resolution in Windows' display settings, and the capture watchdog
        recovered at the new size): the phone's decoder is configured for the
        old size, so the only clean fix is to make it reconnect and take a
        fresh handshake.  Its retry loop does that within a second or two.
        """
        self._drop_client()

    def send_header(self, width, height, fps, note=""):
        if not self.conn:
            return False
        try:
            self.conn.sendall(header(width, height, fps, note))
            self.log("已发送握手 %dx%d@%d%s" % (width, height, fps,
                                             ("  " + note) if note else ""))
            return True
        except OSError as e:
            self.log("握手失败: %s" % e)
            return False

    def _send(self, conn):
        while not self._stop.is_set() and conn is self.conn:
            with self._cv:
                while (not self._queue and not self._stop.is_set()
                       and conn is self.conn):
                    self._cv.wait(0.5)
                batch, self._queue = self._queue, []
                self._queue_bytes = 0
            if conn is not self.conn:
                break
            if not batch:
                continue
            try:
                for u in batch:
                    data = u[0]
                    ts = u[2] if len(u) > 2 else _now_us()
                    conn.sendall(struct.pack("<IQ", len(data), ts))
                    conn.sendall(data)
                    self.stats["sent"] += 1
                    self.stats["bytes"] += len(data)
            except OSError as e:
                self.log("发送中断: %s" % e)
                break

    def put(self, units):
        """units: list of (bytes, is_keyframe[, timestamp_us])"""
        if not units or not self.conn:
            return
        now = _now_us()
        with self._cv:
            for u in units:
                data, key = u[0], u[1]
                ts = u[2] if len(u) > 2 else now
                if self._queue_bytes + len(data) > self.max_queue_bytes:
                    if key:
                        # squeeze it in by dropping older non-keyframes first
                        keep = [q for q in self._queue if q[1]]
                        dropped = len(self._queue) - len(keep)
                        self._queue = keep
                        self._queue_bytes = sum(len(q[0]) for q in keep)
                        self.stats["dropped"] += dropped
                        if self._queue_bytes + len(data) > self.max_queue_bytes:
                            self.stats["dropped"] += 1
                            continue
                    else:
                        self.stats["dropped"] += 1
                        continue
                self._queue.append((data, key, ts))
                self._queue_bytes += len(data)
            self._cv.notify_all()

    @property
    def connected(self):
        return self.conn is not None

    def stop(self):
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        self._drop_client(quiet=True)
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
