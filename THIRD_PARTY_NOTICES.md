# 第三方组件说明

ExamDesk 使用以下主要第三方组件。实际发布版本以构建环境安装的版本和随包许可证为准。

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| PySide6、Shiboken6、Qt | 桌面界面 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| cryptography | 加密和数字签名 | Apache-2.0 OR BSD-3-Clause |
| openpyxl | Excel导入导出 | MIT |
| Pillow | 图片处理 | MIT-CMU |
| python-docx | Word导入 | MIT |
| RapidFuzz | 文本相似度 | MIT |
| ReportLab | PDF报表 | BSD |
| packaging | 版本解析 | Apache-2.0 OR BSD-2-Clause |
| Nuitka | 构建工具，不属于运行时功能 | AGPL-3.0-or-later |

本仓库的`licenses`目录包含需要随发布包提供的通用许可证文本。发布二进制包时还应保留构建工具收集到的各组件版权声明，不得删除Qt动态库或其他依赖自带的许可证文件。
