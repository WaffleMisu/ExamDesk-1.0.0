# ExamDesk 离线考试系统

ExamDesk 是面向 Windows 的离线题库、练习与考试桌面应用。题库维护、组卷、答题、判分、结果复核和报表导出都在本机完成，不依赖服务器或互联网连接。

当前公开版本：`1.0.0`

## 功能

- 单选题、多选题、判断题和多空填空题；
- Excel、Word 和文本题库导入，Excel 题库导出；
- 题图及选项图片、无序填空组和相似答案预估；
- 固定题量随机组卷、限时考试、名单和查看答案策略；
- 逐题反馈练习、错题复盘和图片解析；
- 加密考试包、练习包、答题记录和跨电脑备份恢复；
- 主管理员、离线题库协作、审计和数据维护；
- 可选的考试切屏监控及考生知情确认；
- Excel 成绩汇总和 PDF 个人答卷。

## 运行环境

- Windows 10 x64；
- Python 3.11 x64；
- 推荐使用独立虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m examdesk
```

不要使用 Python 2、32 位 Python 或其他软件附带的旧解释器运行本项目。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests packaging
.\.venv\Scripts\python.exe -m compileall -q src
```

## 打包

安装 Python 3.11 x64 后，可直接双击`一键重新打包.cmd`。首次执行会自动创建项目专用的`.venv311`并安装完整打包依赖，因此首次准备通常需要互联网；以后保留该目录即可离线重新打包。

脚本也会优先使用`EXAMDESK_BUILD_PYTHON`指向的完整构建环境，适合已经准备好依赖的离线电脑。

首次安装依赖默认使用清华大学 PyPI 镜像。也可以设置`EXAMDESK_PIP_INDEX_URL`，或运行 PowerShell 脚本时传入`-PipIndexUrl`切换到其他镜像。

打包时如果项目或虚拟环境路径含中文，脚本会在同一磁盘创建临时 ASCII 硬链接依赖镜像，规避 Nuitka Windows DLL 分析器的路径编码问题；构建完成后会自动清理，不会复制一份完整虚拟环境。

构建结果写入项目内的`release`目录。详细步骤见[自己重新打包说明](docs/自己重新打包说明.txt)。

## 隐私

切屏监控默认关闭。管理员启用后，考生必须在开考前确认监控告知。监控可能记录软件名称、进程名称、窗口标题、切出时间和持续时长。窗口标题可能包含文件名或聊天标题。

完整说明见[PRIVACY.md](PRIVACY.md)。

## 安全边界

ExamDesk 是离线即时判分客户端，不能证明考生运行的是未经修改的软件，也不能单独阻止有能力修改程序或分析本地数据的人员。正式考试应结合现场监督、受控设备和组织管理措施。

漏洞报告方式见[SECURITY.md](SECURITY.md)。

## 许可证

Copyright (C) 2026 WaffleMisu

本项目使用[GNU General Public License v3.0](LICENSE)。第三方组件说明见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
