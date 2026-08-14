param(
    [string]$PythonPath = "",
    [string]$PipIndexUrl = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DeliveryRoot = Join-Path $ProjectRoot "release"
$DistRoot = Join-Path $ProjectRoot "dist_nuitka"

$prepareScript = Join-Path $PSScriptRoot "prepare_build_environment.ps1"
$prepareArgs = @{}
if ($PythonPath) {
    $prepareArgs["PythonPath"] = $PythonPath
}
if ($PipIndexUrl) {
    $prepareArgs["PipIndexUrl"] = $PipIndexUrl
}
$PythonPath = & $prepareScript @prepareArgs
$PythonPath = [string](@($PythonPath)[-1])
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python 3.11 x64 构建环境准备失败。"
}

function Test-AsciiPath([string]$Path) {
    foreach ($character in $Path.ToCharArray()) {
        if ([int][char]$character -gt 127) {
            return $false
        }
    }
    return $true
}

function Remove-ExactGeneratedDirectory([string]$Path, [string]$ExpectedPath) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullExpectedPath = [IO.Path]::GetFullPath($ExpectedPath)
    if (-not $fullPath.Equals($fullExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理非预期目录：$fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

if (-not ("ExamDeskBuild.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace ExamDeskBuild
{
    public static class NativeMethods
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CreateHardLink(
            string newFileName,
            string existingFileName,
            IntPtr securityAttributes
        );
    }
}
"@
}

$buildPythonPath = $PythonPath
$pythonBridgeRoot = ""
$asciiSitePackages = ""
$pythonBridgeCreated = $false
if (-not (Test-AsciiPath $buildPythonPath)) {
    $venvRoot = Split-Path -Parent (Split-Path -Parent (Resolve-Path -LiteralPath $PythonPath).Path)
    $pythonBridgeParent = Split-Path -Parent $venvRoot
    while ($pythonBridgeParent -and -not (Test-AsciiPath $pythonBridgeParent)) {
        $nextParent = Split-Path -Parent $pythonBridgeParent
        if ($nextParent -eq $pythonBridgeParent) {
            break
        }
        $pythonBridgeParent = $nextParent
    }
    if (-not $pythonBridgeParent -or -not (Test-AsciiPath $pythonBridgeParent)) {
        throw "构建环境所在磁盘无法提供 ASCII 路径。请将项目或 Python 环境放到纯英文路径后重试。"
    }
    $pythonBridgeRoot = Join-Path $pythonBridgeParent "ExamDeskNuitkaPython100"
    $sourceSitePackages = Join-Path $venvRoot "Lib\site-packages"
    if (-not (Test-Path -LiteralPath $sourceSitePackages -PathType Container)) {
        throw "未找到 Python 虚拟环境的 site-packages：$sourceSitePackages"
    }
    Remove-ExactGeneratedDirectory $pythonBridgeRoot $pythonBridgeRoot
    New-Item -ItemType Directory -Path $pythonBridgeRoot -Force | Out-Null
    $asciiSitePackages = Join-Path $pythonBridgeRoot "site-packages"
    New-Item -ItemType Directory -Path $asciiSitePackages -Force | Out-Null
    Write-Host "检测到构建路径含中文，正在建立临时 ASCII 依赖路径。"
    $sourceLength = $sourceSitePackages.Length
    try {
        Get-ChildItem -LiteralPath $sourceSitePackages -File -Recurse -Force | ForEach-Object {
            $relativePath = $_.FullName.Substring($sourceLength).TrimStart("\")
            $targetPath = Join-Path $asciiSitePackages $relativePath
            $targetDirectory = Split-Path -Parent $targetPath
            if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) {
                New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
            }
            $linked = [ExamDeskBuild.NativeMethods]::CreateHardLink(
                $targetPath,
                $_.FullName,
                [IntPtr]::Zero
            )
            if (-not $linked) {
                $win32Error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                throw "无法创建临时硬链接：$targetPath（Win32错误 $win32Error）"
            }
        }
    }
    catch {
        Remove-ExactGeneratedDirectory $pythonBridgeRoot $pythonBridgeRoot
        throw
    }
    $pythonBridgeCreated = $true
    Write-Host "临时 ASCII 依赖路径建立完成：$asciiSitePackages"
}

$previousBuildPython = $env:EXAMDESK_BUILD_PYTHON
$previousAsciiSitePackages = $env:EXAMDESK_ASCII_SITE_PACKAGES
try {
    $env:EXAMDESK_BUILD_PYTHON = (Resolve-Path -LiteralPath $PythonPath).Path
    if ($asciiSitePackages) {
        $env:EXAMDESK_ASCII_SITE_PACKAGES = $asciiSitePackages
    }
    Write-Host "使用构建环境：$env:EXAMDESK_BUILD_PYTHON"
    & (Join-Path $PSScriptRoot "build_nuitka.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "EXE构建失败"
    }
}
finally {
    if ($null -eq $previousBuildPython) {
        Remove-Item Env:\EXAMDESK_BUILD_PYTHON -ErrorAction SilentlyContinue
    }
    else {
        $env:EXAMDESK_BUILD_PYTHON = $previousBuildPython
    }
    if ($null -eq $previousAsciiSitePackages) {
        Remove-Item Env:\EXAMDESK_ASCII_SITE_PACKAGES -ErrorAction SilentlyContinue
    }
    else {
        $env:EXAMDESK_ASCII_SITE_PACKAGES = $previousAsciiSitePackages
    }
    if ($pythonBridgeCreated -and (Test-Path -LiteralPath $pythonBridgeRoot)) {
        Remove-ExactGeneratedDirectory $pythonBridgeRoot $pythonBridgeRoot
    }
}

New-Item -ItemType Directory -Path $DeliveryRoot -Force | Out-Null
$releaseZips = Get-ChildItem -LiteralPath $DistRoot -Filter "*.zip"
if ($releaseZips.Count -ne 2) {
    throw "正式版ZIP数量异常：应为2个，实际为$($releaseZips.Count)个"
}
$releaseZips | Copy-Item -Destination $DeliveryRoot -Force

& (Join-Path $PSScriptRoot "build_source.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Python源码包构建失败"
}

$zipFiles = Get-ChildItem -LiteralPath $DeliveryRoot -Filter "*.zip" | Sort-Object Name
if ($zipFiles.Count -ne 3) {
    throw "交付ZIP数量异常：应为3个，实际为$($zipFiles.Count)个"
}
$manifest = $zipFiles | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    "$hash  $($_.Name)"
}
$manifestPath = Join-Path $DeliveryRoot "SHA-256校验值.txt"
[IO.File]::WriteAllLines($manifestPath, $manifest, (New-Object Text.UTF8Encoding($true)))

Write-Host ""
Write-Host "打包完成：$DeliveryRoot"
Get-ChildItem -LiteralPath $DeliveryRoot |
    Select-Object Name, Length, LastWriteTime |
    Sort-Object Name |
    Format-Table -AutoSize
