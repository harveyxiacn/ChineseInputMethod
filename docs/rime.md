# Windows / macOS 系统拼音

双声在 Windows 上使用 [小狼毫 Weasel](https://github.com/rime/weasel/releases)，
在 macOS 上使用 [鼠须管 Squirrel](https://github.com/rime/squirrel/releases)。
两者都是 Rime 官方系统输入法前端；双声提供可安装的独立「双声拼音」方案、
完整词典、安装器及跨平台语音伴侣。普通应用中的预编辑、候选窗和文字上屏由原生前端处理。

## 安装

1. 从上述官方发布页安装对应的 Rime 前端，按其安装说明启用系统输入法。
   macOS 在「系统设置 → 键盘 → 输入法」添加鼠须管；安装后可能需要注销再登录。
2. 解压双声对应平台的 release，打开桌面应用，点击「安装系统拼音…」，
   核对用户目录后安装。也可在终端进入解压目录，运行：

   ```powershell
   # Windows PowerShell
   .\Shuangsheng\Shuangsheng.exe --install-rime
   ```

   ```sh
   # macOS
   ./Shuangsheng.app/Contents/MacOS/Shuangsheng --install-rime
   ```

   发布包中的程序包含安装依赖。源码安装则运行：

   ```sh
   python -m pip install -r requirements-rime.txt
   python scripts/install_rime.py
   ```

3. 在小狼毫右键菜单或鼠须管菜单点击「重新部署」，等待完成。
4. 切换到系统 Rime 输入法，按 `Ctrl+反引号` 或 `F4` 打开方案菜单，选择「双声拼音」。
   自定义了 Rime 方案菜单快捷键的用户沿用自己的快捷键。

安装器默认查找 Windows 的 `%APPDATA%\Rime`（优先尊重 Weasel 注册表中自定义的
`RimeUserDir`），macOS 的 `~/Library/Rime`；Linux 的 Fcitx5-Rime 数据目录为
`${XDG_DATA_HOME:-~/.local/share}/fcitx5/rime`。
可以先检查，或指定前端「用户文件夹」菜单实际打开的路径：

```sh
python scripts/install_rime.py --dry-run
python scripts/install_rime.py --user-dir "/path/to/Rime"
```

`--platform windows|macos|linux` 可覆盖默认平台，用于打包检查；它不安装系统前端。
IBus-Rime 用户可用 `--user-dir ~/.config/ibus/rime` 安装相同方案，再通过 IBus-Rime 重新部署。

## 使用

- 全拼、首字母缩写和连续整句输入；例如 `nihao`、`zhongguo`、`woxihuanzhongguowenhua`。
- 空格选择首选，数字选择候选；`-` / `=`、PageUp / PageDown 翻页。
- Backspace 修改预编辑，Escape 取消；单引号可明确音节边界。
- 默认简体；`Ctrl+Shift+4` 切换繁体。简繁转换采用前端自带 OpenCC。
- Shift 切换中英文；中文逗号、句号和成对引号由方案处理。
- 用户选词学习写入独立的 `shuangsheng.userdb`，由 Rime 负责持久化。

语音伴侣的热键和麦克风权限见项目主文档。拼音方案不需要运行语音服务。

## 保留配置和回退

安装器仅写入当前用户的 Rime 数据目录，使用 `schema_list/+` 追加双声，保留已有
输入方案及其他设置。不会更改 `weasel.custom.yaml`、`squirrel.custom.yaml`、其他
方案的用户词库或 `essay.txt`。

每个将被修改的已有文件都会先备份为
`文件名.before-shuangsheng-UTC时间-随机标识`；原始字节（包括注释和格式）完整保留。
合并后的 `default.custom.yaml` 使用标准 YAML 格式，注释可从备份找回。
重复安装相同版本不重复添加方案、不产生额外备份。无效 YAML 或符号链接会在写入前报错。
更新双声方案前也会备份其旧文件；自己的方案定制建议放在 `shuangsheng.custom.yaml`，
安装器不会写入这个文件。

移除时从 `default.custom.yaml` 的 `schema_list/+` 中移除 `schema: shuangsheng`，
再重新部署。安装后没有修改其他配置时，也可用备份恢复 `default.custom.yaml`。
随后可删除 `shuangsheng.schema.yaml`、`shuangsheng.dict.yaml`、`shuangsheng_essay.txt`
和 `shuangsheng-` 开头的声明/许可证；保留 `shuangsheng.userdb` 可以保留学习结果。

## 数据和验证

发布包离线包含 Luna Pinyin 原始读音词典和 Rime Essay 的 44 万余条词频记录，
使用独立字典名和词频文件名，部署时由 librime 生成词库与拼音索引。
数据许可、固定上游版本及修改范围见 [NOTICE](../native/rime/NOTICE.md)。
它依赖 Weasel/Squirrel 提供 librime 和 OpenCC，不依赖其是否预装了 Luna Pinyin 方案。

安装合并、备份、重复安装和失败回滚测试：

```sh
python -m unittest tests.test_rime_install -v
```

在装有 librime 开发库、OpenCC 数据和 C++17 编译器的环境运行真实引擎测试：

```sh
# Ubuntu/Debian: sudo apt-get install librime-dev libopencc-data cmake g++
cmake -S native/rime -B .cache/rime-build -DCMAKE_BUILD_TYPE=Release
cmake --build .cache/rime-build --parallel
python native/rime/smoke_runtime.py .cache/rime-build/shuangsheng_rime_smoke
```

其他安装位置可在 CMake 配置时设置 `CMAKE_PREFIX_PATH`，测试脚本可设置
`--opencc-dir` 指向包含 `t2s.json` 及词典文件的目录。测试使用临时用户目录，执行真实
部署并检查已有方案保留、候选、简繁转换、整句上屏、编辑及标点。
自动测试不模拟 Windows/macOS 的应用界面；发布前仍建议在记事本/TextEdit、浏览器和
聊天应用中检查候选窗位置、方案切换及文字上屏。

配置依据：[Rime 官方配置说明](https://github.com/rime/home/wiki/Configuration)、
[Weasel 用户文件夹说明](https://github.com/rime/weasel/wiki/Weasel-%E5%AE%9A%E5%88%B6%E5%8C%96)。
