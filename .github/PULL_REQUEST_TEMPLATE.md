## 改了什么 / What changed

## 为什么 / Why

## 怎么验证的 / How it was verified
<!-- 真机测过？只跑了测试脚本？贴一下关键输出
     Tested on a real device? Only ran a test script? Paste the key output -->

## 自查 / Self-check
- [ ] 没有在 `host/`、`android/src/`、`tools/*.ps1` 里写死我这台机器的路径
- [ ] No paths from my own machine were hard-coded in `host/`, `android/src/` or `tools/*.ps1`
- [ ] `python -m compileall host tools` 通过
- [ ] `python -m compileall host tools` passes
- [ ] 改了 `host/version.py` 的话，`tools/installer/DroidExtend.iss` 的 `AppVer` 也跟上了
- [ ] If `host/version.py` was changed, `AppVer` in `tools/installer/DroidExtend.iss` was updated to match
- [ ] 用户能看到的行为变化，写进了 `开发日志.md`
- [ ] User-visible behaviour changes were written into `开发日志.md`
