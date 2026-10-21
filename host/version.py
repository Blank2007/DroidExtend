"""Single place for the version string and the GitHub coordinates.

Everything that needs to know "who am I and where do I live" -- the title bar,
the update checker, the 关于 dialog, the README links -- reads it from here, so
bumping a release is a one-line change.
"""

import i18n

APP_NAME = "DroidExtend"


def app_title():
    """The window title, in the language that is active *now*.

    A module-level constant cannot do this job: app.py imports this file at line
    64 but only calls `i18n.set_lang()` inside the window's `__init__` (line 212),
    so anything evaluated at import time is always Chinese.  app.py should call
    this after `set_lang()` -- once when building the window, and again from
    `_refresh_captions()` so a runtime language switch reaches the title bar too.
    """
    return i18n.t("安卓拓展屏", "Android Extended Screen")


# Kept for `from version import APP_TITLE`: correct for the language that was
# active when this module was first imported, and it is what the packaged
# dialogs print.  Prefer app_title() wherever the title is actually rendered.
APP_TITLE = app_title()

APP_VERSION = "1.7.0"
APP_DATE = "2026-10-21"

REPO = "Blank2007/DroidExtend"
REPO_URL = "https://github.com/" + REPO
RELEASES_URL = REPO_URL + "/releases"
ISSUES_URL = REPO_URL + "/issues"
LATEST_RELEASE_API = "https://api.github.com/repos/%s/releases/latest" % REPO

# Shown in 关于 and in the README, in this order.
AUTHOR = "AI 编码代理（DeepSeek Harness / deepseek-flash）"
OWNER = "Void_Blank (Blank2007)"
EMAILS = ("1553008865@qq.com", "lsw1553008865@gmail.com")
USER_AGENT = "%s/%s (+%s)" % (APP_NAME, APP_VERSION, REPO_URL)
