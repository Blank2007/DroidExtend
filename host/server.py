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

# Before it reads the handshake the phone may send one line in the other
# direction: "DXH1 <panel hz>\n", the fastest rate its screen can actually
# show.  The host needs it *before* ffmpeg starts, because the capture rate
# has to be chosen once -- sending more frames than the phone can decode does
# not make the picture smoother, it makes it freeze (see Session._wanted_fps).
# A 1.6.x host never reads this direction at all, so a new app on an old PC is
# harmless; an old app on this host simply times out and the configured fps
# is used, exactly as before.
HELLO_MAGIC = b"DXH1"
# 1.6 s, not 0.8: measured here, the first connect after adb reverse is set
# up had its line show up about a second late, so a short window started ffmpeg
# at the wrong rate every time the phone was newly plugged in.  An older app
# costs this much per connect and nothing else.
HELLO_TIMEOUT = 1.6      # how long the handshake waits for it
HELLO_LATE = 3.0         # how long a background thread keeps listening
HELLO_MAX = 32


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
        # The panel refresh rate the current phone announced, 0 while unknown.
        self.client_hz = 0

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
            self.client_hz, hello_left = self._read_hello(conn)
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
            if self.client_hz <= 0:
                # The line can be late -- the very first connect after adb
                # reverse is set up sometimes loses its inline bytes.  Keep
                # listening in the background instead of starting the encoder
                # at the wrong rate for the whole session.
                threading.Thread(target=self._hello_watch, args=(conn, hello_left),
                                 name="hello-late", daemon=True).start()
            self.on_client(addr)
        self.log("监听线程退出")

    def _read_hello(self, conn, timeout=HELLO_TIMEOUT):
        """Read the phone's "DXH1 <hz>" line, if it sends one.

        Returns (hz, buf): the panel refresh rate the phone reports -- 0 when it
        said nothing (an older app) or something we do not understand -- and
        whatever bytes arrived.  This runs on the accept thread before the
        handshake is sent, so a silent phone costs exactly `timeout` and
        nothing else; the leftovers go to _hello_watch, which keeps listening
        without holding the handshake up.
        """
        buf = b""
        try:
            conn.settimeout(timeout)
            while len(buf) < HELLO_MAX and b"\n" not in buf:
                chunk = conn.recv(HELLO_MAX - len(buf))
                if not chunk:
                    break
                buf += chunk
        except OSError:
            return 0, buf
        finally:
            try:
                conn.settimeout(None)
            except OSError:
                pass
        hz = self._parse_hello(buf)
        if hz:
            self.log("手机面板刷新率 %dHz" % hz)
        return hz, buf

    @staticmethod
    def _parse_hello(buf):
        line = buf.split(b"\n", 1)[0].strip()
        if not line.startswith(HELLO_MAGIC):
            return 0
        try:
            hz = int(float(line[len(HELLO_MAGIC):].strip() or b"0"))
        except ValueError:
            return 0
        return hz if 1 <= hz <= 1000 else 0

    def _hello_watch(self, conn, buf):
        """Pick up a hello that missed the handshake window.

        Only the video protocol travels PC->phone, so the sole thing this can
        swallow is a second hello from the same app -- the same number anyway.
        It stops as soon as a line arrives, the phone goes away, the connection
        is replaced, or HELLO_LATE passes with nothing.
        """
        try:
            conn.settimeout(HELLO_LATE)
            while self.conn is conn and len(buf) < HELLO_MAX and b"\n" not in buf:
                chunk = conn.recv(HELLO_MAX - len(buf))
                if not chunk:
                    return
                buf += chunk
        except OSError:
            return
        finally:
            try:
                conn.settimeout(None)
            except OSError:
                pass
        hz = self._parse_hello(buf)
        if hz <= 0 or self.conn is not conn:
            return
        self.client_hz = hz
        self.log("手机面板刷新率 %dHz（握手之后才到）" % hz)

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
