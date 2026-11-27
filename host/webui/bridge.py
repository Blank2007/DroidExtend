"""The thin layer the page is allowed to call.

Every method here returns plain data: pywebview turns the return value into
JSON, so nothing that is not a dict/list/str/number may escape.  The window
commands live here too because the page has no way to reach Win32.
"""
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
        self.core = core
        self.win = win          # window.Win, once the handle is known
        self.view = None        # the pywebview window, for quit/dialogs
        self.tray = None
        self._tray_q = queue.Queue()
        self._tray_thread = None

    # ------------------------------------------------------------- the page
    def snapshot(self):
        try:
            return self.core.snapshot()
        except Exception:
            return {"error": traceback.format_exc(limit=2)}

    def logs(self, seq=0):
        try:
            return self.core.log_since(seq)
        except Exception:
            return []

    def act(self, name, payload=None):
        """One entry point for every button on the page."""
        payload = payload or {}
        c = self.core
        try:
            if name == "set":
                return c._set_field(payload.get("field"), payload.get("value"))
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
                c.check_update(); return True
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
        v = self.view
        ok = True
        if v is not None:
            try:
                import webview
                ok = bool(v.create_confirmation_dialog(
                    "重置设置", "把界面上的参数恢复成默认值，并删掉已保存的设置文件。\n\n继续吗？"))
            except Exception:
                ok = True
        if ok:
            self.core.reset_settings()

    def quit_app(self):
        """Close for real: the page asked for it and the settings agree."""
        self.core._closing = True
        self.stop_tray()
        try:
            self.core.session.stop()
        except Exception:
            pass
        view = self.view
        if view is not None:
            try:
                view.destroy()
            except Exception:
                pass

    # ---------------------------------------------------------------- the tray
    def start_tray(self):
        """Put the icon in the notification area; False if this system has none."""
        if self.tray is not None:
            return True
        try:
            import tray as tm
        except Exception:
            return False
        if not tm.available():
            self.core.log("这个系统上用不了托盘图标。", "err")
            return False
        icon = None
        for cand in ("icon.ico", "DroidExtend.ico", "app.ico"):
            p = os.path.join(self.core.app_root, cand)
            if os.path.isfile(p):
                icon = p
                break
        self.tray = tm.Tray(
            tip="%s v%s" % (self.core.app_name, self.core.version),
            on_command=self._tray_q.put,
            icon=icon,
            items=[(tm.CMD_SHOW, "显示主窗口"),
                   (None, None),
                   (tm.CMD_QUIT, "完全退出")],
            log=self.core.log)
        if not self.tray.start():
            self.tray = None
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
                self.core.log("托盘命令失败: %s" % traceback.format_exc(limit=1), "err")

    def stop_tray(self):
        t, self.tray = self.tray, None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def show_window(self):
        if self.win is not None:
            try:
                self.win.show()
            except Exception:
                pass
        v = self.view
        if v is not None:
            try:
                v.show()
            except Exception:
                pass

    def hide_to_tray(self):
        """Hide to the notification area; a system without one just minimizes."""
        if not self.start_tray():
            if self.win is not None:
                self.win.minimize()
            return False
        self.core.log("已缩到托盘，双击托盘图标可以叫回来。")
        if self.win is not None:
            self.win.hide()
        return True

    def close_asked(self):
        """The window's X: ask / hide to the tray / quit, per the settings."""
        action = str(self.core.settings.get("close_action", "ask") or "ask")
        if action == "quit":
            self.quit_app()
            return "quit"
        if action == "tray":
            self.hide_to_tray()
            return "tray"
        return "ask"

    # ----------------------------------------------------------- the window
    def drag(self, hit):
        if self.win is not None:
            fx.Dragger(self.win).start(int(hit))
        return True

    def win(self, action):
        w = self.win
        if w is None:
            return False
        if action == "min":
            w.minimize()
        elif action == "max":
            w.toggle_max()
        elif action == "close":
            return self.close_asked()
        return True