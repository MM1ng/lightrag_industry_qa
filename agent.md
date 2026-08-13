# industrial_energy_agent 开发环境

## Python 环境

- Conda 环境名：`industrial-rag`
- Windows Python 路径：`C:\Users\12189\.conda\envs\industrial-rag\python.exe`
- 项目要求 Python：`>=3.11,<3.12`

## PowerShell 常用命令

```powershell
conda activate industrial-rag
python -m pytest -q
python -m ruff check .
```

如果新窗口尚未正确激活 Conda，可直接使用项目环境解释器：

```powershell
& "C:\Users\12189\.conda\envs\industrial-rag\python.exe" -m pytest -q
& "C:\Users\12189\.conda\envs\industrial-rag\python.exe" -m ruff check .
```

项目测试配置已在 `pyproject.toml` 中声明，运行测试时应优先使用上述 `industrial-rag` 环境。
