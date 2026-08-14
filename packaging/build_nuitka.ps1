$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = $env:EXAMDESK_BUILD_PYTHON
if (-not $Python) {
    $Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到 Python 3.11 构建环境：$Python"
}
$AsciiSitePackages = $env:EXAMDESK_ASCII_SITE_PACKAGES
if ($AsciiSitePackages -and -not (Test-Path -LiteralPath $AsciiSitePackages -PathType Container)) {
    throw "未找到临时 ASCII 依赖路径：$AsciiSitePackages"
}

$StageRoot = Join-Path $env:LOCALAPPDATA "ExamDeskNuitkaBuild100"
$BuildRoot = Join-Path $StageRoot "output"
$DistRoot = Join-Path $ProjectRoot "dist_nuitka"
$SourceRoot = Join-Path $ProjectRoot "src"
$StageIcon = Join-Path $StageRoot "app.ico"
$EntrySource = Join-Path $PSScriptRoot "nuitka_entry.py"
$StageEntry = Join-Path $StageRoot "nuitka_entry.py"
$PreviousPythonPath = $env:PYTHONPATH
if ($AsciiSitePackages) {
    $env:PYTHONPATH = $AsciiSitePackages
}
$Editions = @(
    [PSCustomObject]@{
        Key = "admin"
        Label = "主管理员版"
        ExeName = "ExamDesk_主管理员版.exe"
        FolderName = "ExamDesk_主管理员版"
        ZipName = "ExamDesk_1.0.0_主管理员版_Windows10_x64.zip"
        IncludeTemplate = $true
    },
    [PSCustomObject]@{
        Key = "candidate"
        Label = "考生协作版"
        ExeName = "ExamDesk_考生协作版.exe"
        FolderName = "ExamDesk_考生协作版"
        ZipName = "ExamDesk_1.0.0_考生协作版_Windows10_x64.zip"
        IncludeTemplate = $true
    }
)

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

Write-Host "[1/8] 生成多尺寸应用图标"
& $Python (Join-Path $PSScriptRoot "create_icon.py")
if ($LASTEXITCODE -ne 0) { throw "应用图标生成失败" }

Write-Host "[2/8] 运行自动测试"
& $Python -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest 未通过" }

Write-Host "[3/8] 运行 Ruff"
$Ruff = Join-Path (Split-Path -Parent $Python) "ruff.exe"
& $Ruff check src tests packaging/create_icon.py packaging/nuitka_entry.py
if ($LASTEXITCODE -ne 0) { throw "Ruff 未通过" }

Write-Host "[4/8] 清理并准备 Nuitka 生成目录"
Remove-ExactGeneratedDirectory $StageRoot (Join-Path $env:LOCALAPPDATA "ExamDeskNuitkaBuild100")
Remove-ExactGeneratedDirectory $DistRoot (Join-Path $ProjectRoot "dist_nuitka")
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "app.ico") -Destination $StageIcon -Force
Copy-Item -LiteralPath $EntrySource -Destination $StageEntry -Force

$BuiltFiles = @()
try {
    foreach ($Edition in $Editions) {
        $step = if ($Edition.Key -eq "admin") { "5/8" } else { "6/8" }
        Write-Host "[$step] 构建 $($Edition.Label)"
        $EditionSourceRoot = Join-Path $StageRoot "src_$($Edition.Key)"
        Copy-Item -LiteralPath $SourceRoot -Destination $EditionSourceRoot -Recurse -Force
        $EditionModule = Join-Path $EditionSourceRoot "examdesk\edition.py"
        $AdminEnabled = if ($Edition.Key -eq "admin") { "True" } else { "False" }
        @(
            "from __future__ import annotations",
            "",
            "ADMIN_ENABLED = $AdminEnabled",
            "",
            "",
            "def admin_enabled() -> bool:",
            "    return ADMIN_ENABLED"
        ) | Set-Content -LiteralPath $EditionModule -Encoding ASCII
        if ($AsciiSitePackages) {
            $env:PYTHONPATH = "$EditionSourceRoot;$AsciiSitePackages"
        }
        else {
            $env:PYTHONPATH = $EditionSourceRoot
        }
        $EditionBuildRoot = Join-Path $BuildRoot $Edition.Key
        New-Item -ItemType Directory -Path $EditionBuildRoot -Force | Out-Null
        $NuitkaArgs = @(
            "--mode=standalone",
            "--zig",
            "--assume-yes-for-downloads",
            "--clean-cache=all",
            "--disable-cache=all",
            "--enable-plugins=pyside6",
            "--windows-console-mode=hide",
            "--windows-icon-from-ico=$StageIcon",
            "--company-name=WaffleMisu",
            "--product-name=ExamDesk 离线考试系统 $($Edition.Label)",
            "--file-description=ExamDesk 离线考试系统 $($Edition.Label)",
            "--copyright=Copyright (C) 2026 WaffleMisu",
            "--file-version=1.0.0.0",
            "--product-version=1.0.0.0",
            "--output-filename=$($Edition.ExeName)",
            "--output-dir=$EditionBuildRoot",
            "--nofollow-import-to=tkinter,matplotlib,numpy,pandas",
            $StageEntry
        )
        & $Python -m nuitka @NuitkaArgs
        if ($LASTEXITCODE -ne 0) { throw "$($Edition.Label) Nuitka 构建失败" }

        $CompiledRoot = Get-ChildItem -LiteralPath $EditionBuildRoot -Directory -Filter "*.dist" |
            Select-Object -First 1
        if (-not $CompiledRoot) {
            throw "未找到 $($Edition.Label) Nuitka standalone 输出目录"
        }

        $AppRoot = Join-Path $DistRoot $Edition.FolderName
        New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $CompiledRoot.FullName "*") -Destination $AppRoot -Recurse -Force
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs\使用说明.txt") -Destination $AppRoot -Force
        foreach ($notice in @("LICENSE", "THIRD_PARTY_NOTICES.md", "PRIVACY.md")) {
            Copy-Item -LiteralPath (Join-Path $ProjectRoot $notice) -Destination $AppRoot -Force
        }
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses") -Destination $AppRoot -Recurse -Force
        if ($Edition.IncludeTemplate) {
            $TemplateRoot = Join-Path $AppRoot "templates"
            New-Item -ItemType Directory -Path $TemplateRoot -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $ProjectRoot "templates\ExamDesk_题库维护模板.xlsx") `
                -Destination $TemplateRoot -Force
        }
        $ZipPath = Join-Path $DistRoot $Edition.ZipName
        Compress-Archive -LiteralPath $AppRoot -DestinationPath $ZipPath -CompressionLevel Optimal -Force
        $BuiltFiles += Join-Path $AppRoot $Edition.ExeName
        $BuiltFiles += $ZipPath
    }
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}

Write-Host "[7/8] 两个发行版本整理完成"
Write-Host "[8/8] 输出 SHA-256"
foreach ($Path in $BuiltFiles) {
    $File = Get-Item -LiteralPath $Path
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Path
    Write-Host $File.FullName
    Write-Host "大小：$($File.Length) 字节"
    Write-Host "SHA-256：$($Hash.Hash)"
}

