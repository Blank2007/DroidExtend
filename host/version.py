"""Single place for the version string and the GitHub coordinates.

Everything that needs to know "who am I and where do I live" -- the title bar,
the update checker, the 关于 dialog, the README links -- reads it from here, so
bumping a release is a one-line change.
"""

APP_NAME = "DroidExtend"
APP_TITLE = "安卓拓展屏"
APP_VERSION = "1.4.1"
APP_DATE = "2026-10-12"

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
