# -*- coding: utf-8 -*-
"""Read an APK's version without aapt: zip + the binary AndroidManifest.xml.

The installer ships dist/DroidExtend.apk next to the exe, and nothing in the
program knew which version that file was -- so "is the phone's app older than
the one we just built?" could not be asked.  1.8.5 asks it every time, and the
answer has to come from the APK itself: a sidecar version file would go missing
the first time someone copied just the .apk around (which is exactly how this
program is handed to people in the first place).
"""

import struct
import zipfile

_CHUNK_STRING_POOL = 0x0001
_CHUNK_START_TAG = 0x0102
_NO_INDEX = 0xFFFFFFFF
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11


def _u8len(data, i):
    """Android's 1-or-2 byte length prefix."""
    b = data[i]
    if b & 0x80:
        return ((b & 0x7F) << 8) | data[i + 1], i + 2
    return b, i + 1


def _string_pool(data, off):
    header_size = struct.unpack_from("<H", data, off + 2)[0]
    count = struct.unpack_from("<I", data, off + 8)[0]
    flags = struct.unpack_from("<I", data, off + 16)[0]
    strings_start = struct.unpack_from("<I", data, off + 20)[0]
    utf8 = bool(flags & (1 << 8))
    base = off + strings_start
    out = []
    for k in range(count):
        o = struct.unpack_from("<I", data, off + header_size + 4 * k)[0]
        q = base + o
        if utf8:
            _, q = _u8len(data, q)
            n, q = _u8len(data, q)
            out.append(data[q:q + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, q)[0]
            q += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, q)[0]
                q += 2
            out.append(data[q:q + 2 * n].decode("utf-16-le", "replace"))
    return out


def _string(strings, idx):
    if idx == _NO_INDEX or idx >= len(strings):
        return None
    return strings[idx]


def _parse_manifest(data):
    strings = []
    p = 8                     # the XML chunk's own 8-byte header
    total = len(data)
    while p + 16 <= total:
        ctype = struct.unpack_from("<H", data, p)[0]
        chunk_size = struct.unpack_from("<I", data, p + 4)[0]
        if chunk_size < 8:
            break
        if ctype == _CHUNK_STRING_POOL:
            strings = _string_pool(data, p)
        elif ctype == _CHUNK_START_TAG:
            name = _string(strings, struct.unpack_from("<I", data, p + 20)[0])
            if name == "manifest":
                return _manifest_attrs(data, p, strings)
        p += chunk_size
    return None


def _manifest_attrs(data, p, strings):
    attr_start = struct.unpack_from("<H", data, p + 24)[0]
    attr_size = struct.unpack_from("<H", data, p + 26)[0]
    attr_count = struct.unpack_from("<H", data, p + 28)[0]
    base = p + 16 + attr_start
    code = None
    name = None
    for k in range(attr_count):
        a = base + k * attr_size
        aname = _string(strings, struct.unpack_from("<I", data, a + 4)[0])
        raw = struct.unpack_from("<i", data, a + 8)[0]
        dtype = data[a + 15]
        dvalue = struct.unpack_from("<I", data, a + 16)[0]
        if aname == "versionCode":
            code = dvalue if dtype in (_TYPE_INT_DEC, _TYPE_INT_HEX) else None
        elif aname == "versionName":
            if raw >= 0:
                name = _string(strings, raw)
            elif dtype == _TYPE_STRING:
                name = _string(strings, dvalue)
    return code, name


def read_apk_version(path):
    """(versionCode, versionName) of an APK file, or (None, None) if unreadable."""
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("AndroidManifest.xml")
    except Exception:
        return None, None
    try:
        return _parse_manifest(data) or (None, None)
    except Exception:
        return None, None
