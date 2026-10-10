"""Small parallel downloader with optional proxy fallback.

The PowerShell side of this machine cannot do TLS at all (schannel throws
SEC_E_NO_CREDENTIALS), so every byte we pull down goes through CPython's
OpenSSL.  A Clash-style proxy is usually listening on 127.0.0.1:7897 even when
the system proxy is switched off, and it is often worth 50x on github.
"""
import os
import sys
import time
import urllib.request
import urllib.error

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) droid-extend/1.0"
PROXY = os.environ.get("DE_PROXY", "http://127.0.0.1:7897")


def _opener(proxy=None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def probe(proxy=None, timeout=8):
    """Return True if the proxy actually accepts connections."""
    if not proxy:
        return True
    try:
        op = _opener(proxy)
        op.open("http://www.gstatic.com/generate_204", timeout=timeout).read()
        return True
    except Exception:
        return False


def open_url(url, proxy=None, timeout=30, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return _opener(proxy).open(req, timeout=timeout)


def get(url, outfile, proxy=None, tries=3, label=None, quiet=False):
    """Download url -> outfile. Returns (bytes, seconds)."""
    label = label or os.path.basename(outfile)
    part = outfile + ".part"
    for attempt in range(1, tries + 1):
        done = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": "bytes=%d-" % done} if done else {}
        try:
            t0 = time.time()
            with open_url(url, proxy, headers=headers) as r:
                total = int(r.headers.get("Content-Length") or 0) + done
                mode = "ab" if (done and r.status == 206) else "wb"
                if mode == "wb":
                    done = 0
                with open(part, mode) as f:
                    last = t0
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if not quiet and now - last > 2.0:
                            last = now
                            rate = done / max(now - t0, 1e-6) / 1024
                            pct = (" %5.1f%%" % (done * 100.0 / total)) if total else ""
                            print("  %-42s %7.1f/%-7.1f MB%s %6.0f KB/s"
                                  % (label, done / 1e6, total / 1e6, pct, rate), flush=True)
            if os.path.exists(outfile):
                os.remove(outfile)
            os.replace(part, outfile)
            return os.path.getsize(outfile), time.time() - t0
        except Exception as e:
            if attempt == tries:
                raise
            print("  retry %d/%d %s: %s" % (attempt, tries, label, e), flush=True)
            time.sleep(1.5)
    raise RuntimeError("unreachable")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    px = PROXY if probe(PROXY) else None
    print("proxy:", px, flush=True)
    n, dt = get(src, dst, proxy=px)
    print("done %.1f MB in %.1fs (%.0f KB/s)" % (n / 1e6, dt, n / dt / 1024), flush=True)
