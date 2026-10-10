import ctypes
from ctypes import wintypes

adv = ctypes.windll.advapi32
k32 = ctypes.windll.kernel32

TOKEN_QUERY = 0x0008
TokenIsAppContainer = 29
TokenIntegrityLevel = 25
TokenRestrictedSids = 11
TokenGroups = 2
TokenUser = 1

h = wintypes.HANDLE()
print("OpenProcessToken:", adv.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(h)))

buf = (ctypes.c_char * 4096)()
ret = wintypes.DWORD()
ok = adv.GetTokenInformation(h, TokenIsAppContainer, ctypes.byref(buf), 4096, ctypes.byref(ret))
print("IsAppContainer ok=%s value=%s" % (ok, ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value if ok else "-"))

# integrity level
ok = adv.GetTokenInformation(h, TokenIntegrityLevel, ctypes.byref(buf), 4096, ctypes.byref(ret))
if ok:
    sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    nsub = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[1]
    last = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[8 + 4 * (nsub - 1)]
    print("IntegrityLevel RID=%d (0x%x)  medium=0x2000 high=0x3000 low=0x1000" % (last, last))
else:
    print("integrity query failed")

# restricted sids count
ok = adv.GetTokenInformation(h, TokenRestrictedSids, ctypes.byref(buf), 4096, ctypes.byref(ret))
print("TokenRestrictedSids ok=%s bytes=%s" % (ok, ret.value))

ok = adv.GetTokenInformation(h, TokenGroups, ctypes.byref(buf), 4096, ctypes.byref(ret))
print("TokenGroups ok=%s bytes=%s" % (ok, ret.value))
