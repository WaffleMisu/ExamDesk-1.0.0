# 参与贡献

## 开发环境

使用 Python 3.11 x64 和独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 提交前检查

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests packaging
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pytest
```

新增功能需要测试覆盖。涉及考试包、签名、备份、答题记录或监控行为的修改，应同时更新安全或隐私说明。

请勿提交真实题库、个人信息、组织证书、数据库、答题记录、构建缓存或发布压缩包。
