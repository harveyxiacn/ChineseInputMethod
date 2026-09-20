# 双声桌面版

桌面版提供原生窗口，支持 Windows、macOS 和 Linux。在窗口中输入拼音选词，或使用普通话/粤语本地听写，编辑后点击“复制全部”，再切换到目标应用粘贴。它是中文输入与听写伴侣；Windows/macOS 的系统拼音输入通过发行包内的 Rime 配置安装。桌面版不会自动向其他窗口发送按键或插入文字。

## 使用发行包

从 GitHub Releases 下载对应系统和处理器架构的压缩包，完整解压后运行 `Shuangsheng`（Windows 为 `Shuangsheng.exe`，macOS 为 `Shuangsheng.app`）。不要单独移动可执行文件，必须保留旁边的依赖和数据目录。

拼音输入开箱可用：输入 `nihao`，按回车选择首个候选；也可以按向下键进入候选列表，按回车选词，或双击候选。切换“简体 / 繁體”只影响之后选择的候选和之后录制的语音，不改写已有文本。

要在其他应用中直接使用系统拼音输入，先安装对应平台的 Rime 前端（Windows 小狼毫 Weasel / macOS 鼠须管 Squirrel / Linux Fcitx5-Rime），然后点击“安装系统拼音…”。确认安装位置后，程序会备份将被替换的已有文件并安装双声方案。最后在输入法菜单中“重新部署”，按 `` Ctrl+` `` 或 `F4` 选择“双声拼音”。此按钮安装输入方案，不替你安装系统输入法前端；自定义目录可使用发行程序的 `--install-rime --user-dir 路径` 命令。

语音需要首次准备模型。点击“下载语音模型…”，确认后下载 `large-v3`；此步骤需要联网、数 GB 的下载空间，完成后可离线听写。不会在启动或录音时自动下载。也可以点击“选择本地模型”，选择已下载、包含 `model.bin` 和 `config.json` 的 faster-whisper/CTranslate2 模型目录；手动选择只对本次运行生效。粤语建议使用 `large-v3`，其他模型可能不支持粤语。

也可以通过命令行准备模型：

```powershell
# Windows，位于解压后的程序目录
.\Shuangsheng.exe --download-model large-v3
```

```bash
# macOS，位于解压后的程序目录
./Shuangsheng.app/Contents/MacOS/Shuangsheng --download-model large-v3
# Linux，位于解压后的程序目录
./Shuangsheng --download-model large-v3
```

首次加载大模型和 CPU 识别可能较慢，需要足够内存。模型下载按钮会显示完成或失败；下载期间拼音与文本编辑仍可用。

## 录音与权限

1. 在“语音”中选择普通话、粤语或自动检测，选择输出简繁。
2. 首次使用时允许系统麦克风权限。默认使用系统默认麦克风；点击“刷新设备”可指定其他设备。
3. 点击“开始录音”，说话后点击“停止并识别”。单次录音最长 115 秒，达到上限自动识别。
4. 检查并修改识别文字，点击“复制全部”，切换到目标应用，用系统粘贴快捷键粘贴。

可用时，全局 `Ctrl+Alt+Space` 开始/停止录音；macOS 对应 `Control+Option+Space`。快捷键不会切换当前应用的焦点。macOS 可能需要在“系统设置 → 隐私与安全性”中授权“辅助功能”及“输入监控”，变更后重启应用。未授权仍可使用窗口按钮。Linux Wayland 下默认禁用全局键盘监听，使用窗口按钮；X11 下取决于桌面权限。限制依据 [pynput 官方平台说明](https://pynput.readthedocs.io/en/latest/limitations.html)。

“取消”或窗口内 `Escape` 丢弃当前录音/结果。若模型正在识别，原生推理需结束后才能再次录音；等待期间不会插入取消的结果。关闭窗口终止当前会话。录音只在内存中处理，识别文本不写入日志；文字仅在主动复制或保存时离开程序内存。

剪贴板繁忙时，文字仍保留在文本区，可以重试复制或“保存文本…”。Linux 的剪贴板可能需要本窗口持续运行，请在关闭窗口前完成粘贴。文本区内容不会自动保存，关闭前请复制或保存需要保留的内容。

## 从源码运行

推荐使用 Python 3.12（或 3.11）：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements-desktop.txt
python -m ime.desktop
```

Linux 还需要 Tk 和 PortAudio，例如 Ubuntu/Debian：

```bash
sudo apt-get install python3-tk libportaudio2
```

`sounddevice` 在 Windows/macOS 的 pip 安装通常自带 PortAudio；其他平台参见 [sounddevice 安装文档](https://python-sounddevice.readthedocs.io/en/latest/installation.html)。从源码运行时，系统麦克风权限可能显示为 Python 或终端程序。

macOS 安装桌面依赖时会自动安装 PyObjC 的 ApplicationServices 与 Quartz 框架绑定；发行包包含这些依赖。程序通过 `HIServices.AXIsProcessTrusted()` 查询已有的辅助功能授权，不自动弹出授权请求。该绑定属于 [pyobjc-framework-ApplicationServices](https://pyobjc.readthedocs.io/en/latest/apinotes/HIServices.html)。

```bash
# 从源码显式下载模型
python -m ime.model_setup --model large-v3
# 禁用可选的全局快捷键监听
python -m ime.desktop --no-hotkey
# 检查发行包资源，无需屏幕、麦克风或网络
python -m ime.desktop --smoke-test
# 打开窗口检查拼音、剪贴板和按钮状态，然后自动退出
python -m ime.desktop --gui-smoke-test
```

支持 `IME_WHISPER_MODEL`（模型名称或本地目录）、`IME_WHISPER_DEVICE`（默认 `cpu`，兼容 CUDA 的环境可自行选择 `cuda`）和 `HF_HOME`。默认使用本机 CPU 的 int8 推理。软件包不包含模型权重，缓存位置由共享语音模块统一管理。

## 常见问题

- **无法录音**：在系统声音设置中确认输入设备可用、未静音并已授权麦克风；更换设备后点击“刷新设备”。程序采用设备默认采样率，最高 48 kHz，并在识别前转为模型需要的音频格式。
- **无法加载模型**：先通过下载按钮准备模型，或选择完整的 faster-whisper 本地模型目录。模型损坏、缺失文件或显存不足也会产生明确错误。选择 CUDA 失败时恢复默认 CPU。
- **录音太短/静音**：至少录制 0.2 秒并确认输入电平；此类录音会直接拒绝，不启动模型识别。
- **录音中断/丢帧**：关闭抢占音频设备的程序，更换设备再试，避免对不完整音频给出误导结果。
- **无法打开窗口**：源码运行需要可用的 Tk 和图形桌面会话；SSH/无显示环境只能运行 `--smoke-test`，或使用项目的浏览器版。

## 开发验证

```bash
python -m unittest tests.test_desktop_voice tests.test_desktop -v
# Linux 无物理显示时运行实际 Tk 窗口测试
xvfb-run -a python -m unittest tests.test_desktop -v
```

硬件无关测试验证音频边界、采样率、静音、权限错误、取消、并发保护及结果传递。窗口测试验证拼音选词、简繁、复制、剪贴板失败时文字保留和取消结果丢弃。麦克风授权、实际录音以及目标应用粘贴仍需在目标操作系统上人工验收。
