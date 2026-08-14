param(
    [string]$PythonPath = "",
    [string]$PipIndexUrl = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectVenv = Join-Path $ProjectRoot ".venv311"
$ProjectPython = Join-Path $ProjectVenv "Scripts\python.exe"
if (-not $PipIndexUrl) {
    $PipIndexUrl = $env:EXAMDESK_PIP_INDEX_URL
}
if (-not $PipIndexUrl) {
    $PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
}

function Test-Python311X64([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    try {
        & $Candidate -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and struct.calcsize('P') * 8 == 64 else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Test-BuildDependencies([string]$Candidate) {
    if (-not (Test-Python311X64 $Candidate)) {
        return $false
    }

    $checkCode = @'
import importlib
from importlib.metadata import version

for module_name in (
    'PySide6', 'cryptography', 'openpyxl', 'PIL', 'packaging', 'docx',
    'rapidfuzz', 'reportlab', 'nuitka', 'pytest', 'zstandard',
    'ordered_set', 'PyInstaller', 'setuptools', 'ziglang',
):
    importlib.import_module(module_name)

for distribution_name in ('ruff', 'nuitka', 'zstandard', 'ziglang'):
    version(distribution_name)
'@

    try {
        & $Candidate -c $checkCode 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        & $Candidate -m ruff --version 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-NormalizedPath([string]$Path) {
    return (Resolve-Path -LiteralPath $Path).Path
}

function Invoke-NativeWithHostOutput([string]$Executable, [string[]]$Arguments) {
    $previousErrorAction = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 may wrap a native program's stderr as an error record.
        # The native exit code is the reliable success signal for pip and venv.
        $ErrorActionPreference = "Continue"
        & $Executable @Arguments 2>&1 | ForEach-Object { Write-Host ([string]$_) }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return $exitCode
}

function Get-LocalPython311Candidates {
    $candidates = @()

    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "Python311\python.exe"
    }

    $registryKeys = @(
        "HKCU:\Software\Python\PythonCore\3.11\InstallPath",
        "HKLM:\Software\Python\PythonCore\3.11\InstallPath",
        "HKLM:\Software\WOW6432Node\Python\PythonCore\3.11\InstallPath"
    )
    foreach ($registryKey in $registryKeys) {
        if (-not (Test-Path -LiteralPath $registryKey)) {
            continue
        }
        try {
            $key = Get-Item -LiteralPath $registryKey
            $executablePath = $key.GetValue("ExecutablePath")
            $installPath = $key.GetValue("")
            if ($executablePath) {
                $candidates += [string]$executablePath
            }
            if ($installPath) {
                $candidates += Join-Path ([string]$installPath) "python.exe"
            }
        }
        catch {
            Write-Host "无法读取 Python 注册表项，已跳过：$registryKey"
        }
    }

    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += $pythonCommand.Source
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $launcherPath = & $pyLauncher.Source -3.11-64 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $launcherPath) {
                $candidates += [string](@($launcherPath)[-1])
            }
        }
        catch {
            Write-Host "Python Launcher 未找到 Python 3.11 x64，已跳过。"
        }
    }

    return $candidates | Where-Object { $_ } | Select-Object -Unique
}

function Remove-ProjectVenvSafely {
    if (-not (Test-Path -LiteralPath $ProjectVenv)) {
        return
    }
    $actual = [IO.Path]::GetFullPath($ProjectVenv).TrimEnd("\")
    $expected = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv311")).TrimEnd("\")
    if (-not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理非预期目录：$actual"
    }
    Write-Host "现有 .venv311 无法使用，正在重新创建。"
    Remove-Item -LiteralPath $actual -Recurse -Force
}

if ($PythonPath) {
    if (-not (Test-Python311X64 $PythonPath)) {
        throw "指定的 Python 不是可用的 Python 3.11 x64：$PythonPath"
    }
    if (-not (Test-BuildDependencies $PythonPath)) {
        throw "指定的 Python 缺少完整打包依赖。请先执行 python -m pip install -e `".[dev]`"。"
    }
    Write-Host "已验证指定的 Python 3.11 x64 构建环境。"
    Write-Output (Get-NormalizedPath $PythonPath)
    return
}

$existingCandidates = @(
    $env:EXAMDESK_BUILD_PYTHON,
    $ProjectPython,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
) | Where-Object { $_ }

foreach ($candidate in $existingCandidates) {
    if (Test-BuildDependencies $candidate) {
        Write-Host "已找到并验证 Python 3.11 x64 构建环境。"
        Write-Output (Get-NormalizedPath $candidate)
        return
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Write-Host "构建环境版本不符或依赖不完整，已跳过：$candidate"
    }
}

if (Test-Python311X64 $ProjectPython) {
    Write-Host "正在为现有项目环境补齐打包依赖：$ProjectPython"
}
else {
    $basePython = Get-LocalPython311Candidates |
        Where-Object { Test-Python311X64 $_ } |
        Select-Object -First 1
    if (-not $basePython) {
        throw "未找到 Python 3.11 x64。请先安装 Python 3.11 x64，或设置 EXAMDESK_BUILD_PYTHON 指向已准备好的构建环境。"
    }

    Remove-ProjectVenvSafely
    Write-Host "首次打包：正在使用本机 Python 3.11 x64 创建 .venv311。"
    $venvExitCode = Invoke-NativeWithHostOutput $basePython @("-m", "venv", $ProjectVenv)
    if ($venvExitCode -ne 0 -or -not (Test-Python311X64 $ProjectPython)) {
        throw "创建 .venv311 失败。"
    }
}

Write-Host "正在安装 ExamDesk 及完整打包依赖。首次准备通常需要互联网，请耐心等待。"
Write-Host "pip 镜像：$PipIndexUrl"
Push-Location $ProjectRoot
try {
    $pipExitCode = Invoke-NativeWithHostOutput $ProjectPython @(
        "-m", "pip", "install", "--index-url", $PipIndexUrl, "-e", ".[dev]"
    )
    if ($pipExitCode -ne 0) {
        throw "依赖安装失败。首次准备需要互联网；离线电脑请设置 EXAMDESK_BUILD_PYTHON，指向已安装完整依赖的 Python 3.11 x64 环境。"
    }
}
finally {
    Pop-Location
}

if (-not (Test-BuildDependencies $ProjectPython)) {
    throw "依赖安装结束，但构建环境验证未通过。请查看上方 pip 输出。"
}

Write-Host "Python 3.11 x64 构建环境准备完成。以后重新打包可直接离线使用该环境。"
Write-Output (Get-NormalizedPath $ProjectPython)
