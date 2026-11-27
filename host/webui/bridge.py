"""The thin layer the page is allowed to call.

Every method here returns plain data: pywebview turns the return value into
JSON, so nothing that is not a dict/list/str/number may escape.  The window
commands live here too because the page has no way to reach Win32.
"""
import inspect
import os
import queue
import threading
import time
import traceback
import webbrowser

import core as core_mod
import window as fx


class Bridge(object):
    def __init__(self, core, win=None):
        self._core = core
        self._win = win          # window.Win, once the handle is known
        self._view = None        # the pywebview window, for quit/dialogs
        self._tray = None
        self._tray_q = queue.Queue()
        self._tray_thread = None
        self._quitting = False

    def __dir__(self):
        # pywebview walks every public attribute of the js_api object and recurses
        # into whatever is not a function.  The window, the core and the tray are
        # objects, so that walk used to crawl from the .NET window into
        # AccessibilityObject.Bounds.Empty... until it hit "maximum recursion
        # depth exceeded" -- and every retry made the message longer, which is how
        # a 10 MB log and a frozen window happened.  Only methods are callable from
        # the page, so only methods are listed.
        out = []
        for n in object.__dir__(self):
            if n.startswith("_"):
                continue
            try:
                v = getattr(self, n)
            except Exception:
                continue
            if inspect.ismethod(v) or inspect.isfunction(v):
                out.append(n)
        return out

    # ------------------------------------------------------------- the page
    def snapshot(self):
        try:
            return self._core.snapshot()
        except Exception:
            return {"error": traceback.format_exc(limit=2)}

    def logs(self, seq=0):
        try:
            return self._core.log_since(seq)
        except Exception:
            return []

    def theme_frame(self):
        """The system draws the border and the title area, so repaint it too."""
        w = self._win
        if w is None:
            return
        dark = self._core.settings.get("theme", "dark") != "light"
        try:
            w.dark_frame(dark)
            w.frame_colors(fx.FRAME_BG_DARK if dark else fx.FRAME_BG_LIGHT,
                           fx.FRAME_LINE_DARK if dark else fx.FRAME_LINE_LIGHT)
        except Exception:
            pass

    def act(self, name, payload=None):
        """One entry point for every button on the page."""
        payload = payload or {}
        c = self._core
        try:
            if name == "set":
                ok = c._set_field(payload.get("field"), payload.get("value"))
                if payload.get("field") == "theme":
                    self.theme_frame()
                return ok
            if name == "start":
                c.start()
                return True
            if name == "stop":
                c.stop()
                return True
            if name == "save_settings":
                c.apply_to_cfg()
                c._save()
                return True
            if name == "log":
                c.log(payload.get("text") or "")
                return True
            if name == "open_url":
                webbrowser.open(payload.get("url") or "")
                return True
            if name == "open_dir":
                try:
                    os.startfile(payload.get("path") or "")
                except Exception:
                    pass
                return True
            if name == "quit":
                self.quit_app()
                return "quit"
            if name == "installDriver":
                c.install_driver(); return True
            if name == "uninstallDriver":
                c.uninstall_driver(); return True
            if name == "diagnose":
                c.diagnose(); return True
            if name == "linkspeed":
                c.linkspeed(); return True
            if name == "testNotify":
                c.test_notify(); return True
            if name == "checkUpdate":
                return c.check_update()
            if name == "installUpdate":
                return c.install_update()
            if name == "saveLog":
                return c.save_log()
            if name == "doc":
                return c.doc(payload.get("which"))
            if name == "cleanupOrphans":
                c.cleanup_orphans(); return True
            if name == "vddOff":
                c.vdd_off(); return True
            if name == "installUsbDriver":
                c.install_usb_driver(); return True
            if name == "installApk":
                c.install_apk(); return True
            if name == "openDir":
                return c.open_dir(payload.get("which") or "config")
            if name == "answer":
                return c.answer_ask(payload.get("id"), payload.get("ok"))
            if name == "privacy":
                return c.privacy()
            if name == "acceptTerms":
                return c.accept_terms()
            if name == "autoStream":
                c.auto_stream(); return True
            if name == "resetSettings":
                self._reset_settings()
                return True
            return False
        except Exception:
            c.log("操作失败: %s" % traceback.format_exc(limit=2).strip().splitlines()[-1], "err")
            return False

    def _reset_settings(self):
        """Ask first, then hand it to Core -- the old button had the same two steps."""
        v = self._view
        ok = True
        if v is not None:
            try:
                import webview
                ok = bool(v.create_confirmation_dialog(
                    "重置设置", "把界面上的参数恢复成默认值，并删掉已保存的设置文件。\n\n继续吗？"))
            except Exception:
                ok = True
        if ok:
            self._core.reset_settings()

    def quit_app(self):
        """Close for real: the page asked for it and the settings agree."""
        self._quitting = True
        self._core._closing = True
        self.stop_tray()
        try:
            self._core.session.stop()
        except Exception:
            pass
        view = self._view
        if view is not None:
            try:
                view.destroy()
            except Exception:
                pass

    # ---------------------------------------------------------------- the tray
    def start_tray(self):
        """Put the icon in the notification area; False if this system has none."""
        if self._tray is not None:
            return True
        try:
            import tray as tm
        except Exception:
            return False
        if not tm.available():
            self._core.log("这个系统上用不了托盘图标。", "err")
            return False
        icon = None
        for cand in ("icon.ico", "DroidExtend.ico", "app.ico"):
            p = os.path.join(self._core.app_root, cand)
            if os.path.isfile(p):
                icon = p
                break
        self._tray = tm.Tray(
            tip="%s v%s" % (self._core.app_name, self._core.version),
            on_command=self._tray_q.put,
            icon=icon,
            items=[(tm.CMD_SHOW, "显示主窗口"),
                   (None, None),
                   (tm.CMD_QUIT, "完全退出")],
            log=self._core.log)
        if not self._tray.start():
            self._tray = None
            return False
        self._tray_thread = threading.Thread(target=self._tray_loop,
                                             name="dx-tray", daemon=True)
        self._tray_thread.start()
        return True

    def _tray_loop(self):
        import tray as tm
        while True:
            cmd = self._tray_q.get()
            try:
                if cmd == tm.CMD_SHOW:
                    self.show_window()
                elif cmd == tm.CMD_QUIT:
                    self.quit_app()
                    return
            except Exception:
                self._core.log("托盘命令失败: %s" % traceback.format_exc(limit=1), "err")

    def stop_tray(self):
        t, self._tray = self._tray, None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def show_window(self):
        if self._win is not None:
            try:
                self._win.show()
            except Exception:
                pass
        v = self._view
        if v is not None:
            try:
                v.show()
            except Exception:
                pass

    def hide_to_tray(self):
        """Hide to the notification area; a system without one just minimizes."""
        if not self.start_tray():
            if self._win is not None:
                self._win.minimize()
            return False
        self._core.log("已缩到托盘，双击托盘图标可以叫回来。")
        if self._win is not None:
            self._win.hide()
        return True

    def on_closing(self):
        """Alt+F4 and the taskbar's Close take the same path as our own X."""
        if self._quitting:
            return True
        try:
            r = self.close_asked()
        except Exception:
            return True
        if r == "ask" and self._view is not None:
            # Alt+F4 on a window whose answer is "ask" gets the same card the X
            # opens, not a stuck window.
            try:
                self._view.evaluate_js("window.askClose && window.askClose()")
            except Exception:
                pass
        return False

    def close_asked(self):
        """The window's X: ask / hide to the tray / quit, per the settings."""
        action = str(self._core.settings.get("close_action", "ask") or "ask")
        if action == "quit":
            self.quit_app()
            return "quit"
        if action == "tray":
            self.hide_to_tray()
            return "tray"
        return "ask"

    # ----------------------------------------------------------- the window
    def drag(self, hit):
        """Let Windows run the drag loop: snap, monitor hopping, everything."""
        if self._win is not None:
            self._win.drag(int(hit) if hit is not None else None)
        return True

    def win(self, action):
        w = self._win
        if w is None:
            return False
        if action == "min":
            w.minimize()
        elif action == "max":
            w.toggle_max()
        elif action == "dbl":
            w.double_click()
        elif action == "menu":
            w.system_menu()
        elif action == "close":
            return self.close_asked()
        return True