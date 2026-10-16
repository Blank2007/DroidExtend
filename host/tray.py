"""系统托盘图标 —— 纯 ctypes，不引入任何第三方依赖。

为什么不用 pystray：打包机上一个第三方库都没有，而这个程序要保持"解压即用"。
Shell_NotifyIcon 本身只要一个窗口和一个消息循环，用 ctypes 直接调并不比
拖进一个依赖更麻烦。

线程模型：托盘窗口和它的消息循环跑在**自己的线程**里（Windows 要求
Shell_NotifyIcon 的回调消息由创建窗口的那个线程派发）。所有回调都只是把命令
丢进队列，UI 线程用 Tk 的 after() 轮询取走 —— 从别的线程碰 Tk 控件是崩溃的
常见来源。
"""

import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IDI_APPLICATION = 32512
HWND_MESSAGE = wt.HWND(-3)
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040

# 菜单命令号。从 1000 起，免得和系统命令号打架。
CMD_SHOW, CMD_TOGGLE, CMD_QUIT = 1001, 1002, 1003


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt_x", wt.LONG),
        ("pt_y", wt.LONG),
    ]


user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.LoadIconW.restype = wt.HICON
user32.LoadImageW.restype = wt.HANDLE
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.restype = wt.UINT
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]

# 这些必须写全：ctypes 默认按 c_int 传参，64 位的 HWND 会被截断成 32 位。
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
# 图标资源既可以是名字也可以是 MAKEINTRESOURCE 造的整数，所以按 void* 收。
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadImageW.argtypes = [
    wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL


def load_icon(path=None):
    """给托盘找个图标。优先用程序自己的 .ico，退而求其次用系统默认图标。"""
    if path:
        try:
            h = user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return wt.HICON(h)
        except Exception:
            pass
    try:
        return user32.LoadIconW(None, IDI_APPLICATION)
    except Exception:
        return None


class Tray(object):
    """一个托盘图标 + 一个右键菜单。

    on_command(cmd) 由**托盘线程**调用，实现里只该做线程安全的事（往队列里放东西）。
    """

    def __init__(self, tip, on_command, icon=None, items=None, log=None):
        self.tip = (tip or "DroidExtend")[:127]
        self.on_command = on_command
        self.icon_path = icon
        self.items = items or []           # [(cmd, label)]，label 为 None 表示分隔线
        self.log = log or (lambda m, t=None: None)
        self._thread = None
        self._hwnd = None
        self._hicon = None
        self._proc = None                  # 必须留引用，否则回调被 GC 掉
        self._cls = None
        self._ready = threading.Event()
        self._nid = None

    # ------------------------------------------------------------------ 对外
    def start(self):
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(6.0)
        return self._hwnd is not None

    def stop(self):
        hwnd = self._hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(3.0)
        self._thread = None
        self._hwnd = None

    @property
    def running(self):
        return self._hwnd is not None

    def set_tip(self, text):
        """改提示文字（比如后面挂上"正在投屏"）。"""
        self.tip = (text or "DroidExtend")[:127]
        nid = self._nid
        if nid is None:
            return
        try:
            nid.szTip = self.tip
            nid.uFlags = NIF_TIP
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def balloon(self, title, text):
        nid = self._nid
        if nid is None:
            return
        try:
            nid.uFlags = 0x10                  # NIF_INFO
            nid.szInfoTitle = (title or "")[:63]
            nid.szInfo = (text or "")[:255]
            nid.dwInfoFlags = 0x01             # NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    # ------------------------------------------------------------------ 线程
    def _run(self):
        try:
            self._create()
        except Exception as e:
            self.log("托盘图标创建失败：%s" % e)
            self._ready.set()
            return

        msg = MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._nid is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None
        if self._hwnd:
            try:
                user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = None

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "DroidExtendTrayWnd"

        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = load_icon(self.icon_path)
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = cls_name
        self._cls = wc
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err not in (0, 1410):          # 1410 = 类已注册
                raise OSError("RegisterClassW 失败 (%d)" % err)

        hwnd = user32.CreateWindowExW(0, cls_name, cls_name, 0, 0, 0, 0, 0,
                                      HWND_MESSAGE, None, hinst, None)
        if not hwnd:
            raise OSError("CreateWindowExW 失败 (%d)" % ctypes.get_last_error())
        self._hwnd = hwnd
        self._hicon = wc.hIcon or load_icon(None)

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = self.tip
        self._nid = nid
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIcon(NIM_ADD) 失败 (%d)" % ctypes.get_last_error())
        self._ready.set()

    # ---------------------------------------------------------------- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._emit(CMD_SHOW)
                    return 0
                if ev == WM_RBUTTONUP:
                    self._menu(hwnd)
                    return 0
            elif msg == WM_COMMAND:
                self._emit(wparam & 0xFFFF)
                return 0
            elif msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            try:
                self.log("托盘消息处理失败：%s" % e)
            except Exception:
                pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for cmd, label in self.items:
                if label is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(menu, MF_STRING, cmd, label)
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # 这两步是 Win32 的既定套路：不先抢前台，菜单点外面不会消失。
            user32.SetForegroundWindow(hwnd)
            choice = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            if choice:
                self._emit(choice)
        finally:
            user32.DestroyMenu(menu)

    def _emit(self, cmd):
        try:
            self.on_command(int(cmd))
        except Exception as e:
            try:
                self.log("托盘命令 %s 处理失败：%s" % (cmd, e))
            except Exception:
                pass


def available():
    """这台机器上托盘能不能用（不是 Windows 就直接说不行）。"""
    try:
        return bool(user32.RegisterClassW) and bool(shell32.Shell_NotifyIconW)
    except Exception:
        return False
