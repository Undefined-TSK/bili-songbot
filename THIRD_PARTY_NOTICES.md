# 第三方软件与代码声明（THIRD_PARTY NOTICES）

本项目自有代码以 **GPL-3.0** 发布（见 LICENSE）。

## lxserver

- 来源：https://github.com/XCQ0607/lxserver
- 本项目通过 HTTP 调用独立运行的 lxserver，**不复制、不分发**其源码。
- lxserver 及其音源脚本的许可、免责声明与使用限制以对应项目为准。

## 音源脚本（`sources/`）

- 目录下的脚本来自第三方社区，仅用于个人学习研究，不随桌面应用打包。
- 脚本可能要求 lxserver 开启 `SYSTEM_ALLOW_UNSAFE_VM=true`；请自行评估安全风险。

## 运行依赖

- `ws`（MIT）、`pinyin-pro`（MIT）、`fuzzball`（MIT）、Electron（MIT）、ffmpeg/ffplay（LGPL/GPL，使用时请遵守其许可）。
