## 改了什么

## 为什么

## 怎么验证的
<!-- 真机测过？只跑了测试脚本？贴一下关键输出 -->

## 自查
- [ ] 没有在 `host/`、`android/src/`、`tools/*.ps1` 里写死我这台机器的路径
- [ ] `python -m compileall host tools` 通过
- [ ] 改了 `host/version.py` 的话，`tools/installer/DroidExtend.iss` 的 `AppVer` 也跟上了
- [ ] 用户能看到的行为变化，写进了 `开发日志.md`
