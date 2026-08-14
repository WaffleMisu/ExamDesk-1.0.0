param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [Parameter(Mandatory = $true)]
    [string]$Wheelhouse,
    [Parameter(Mandatory = $true)]
    [string]$NuitkaResources,
    [Parameter(Mandatory = $true)]
    [string]$DeliveryRoot
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = $PythonPath
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到Python 3.11离线编译环境：$Python"
}
if (-not (Test-Path -LiteralPath $Wheelhouse)) {
    throw "未找到离线依赖目录：$Wheelhouse"
}
$DownloadResources = Join-Path $NuitkaResources "downloads"
if (-not (Test-Path -LiteralPath (Join-Path $DownloadResources "depends\x86_64\depends.exe"))) {
    throw "Nuitka离线资源不完整：$DownloadResources"
}

$StageRoot = Join-Path $env:LOCALAPPDATA "ExamDeskNuitkaOfflineBuild100"
$BuildRoot = Join-Path $StageRoot "output"
$DistRoot = Join-Path $ProjectRoot "dist_nuitka"
$SourceRoot = Join-Path $ProjectRoot "src"
$StageIcon = Join-Path $StageRoot "app.ico"
$EntrySource = Join-Path $PSScriptRoot "nuitka_entry.py"
$StageEntry = Join-Path $StageRoot "nuitka_entry.py"
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

function Restore-EnvironmentVariable([string]$Name, $PreviousValue) {
    if ($null -eq $PreviousValue) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -LiteralPath "Env:$Name" -Value $PreviousValue
    }
}

Write-Host "[1/9] 恢复Nuitka离线资源"
$NuitkaDownloadCache = Join-Path $env:LOCALAPPDATA "Nuitka\Nuitka\Cache\downloads"
New-Item -ItemType Directory -Path $NuitkaDownloadCache -Force | Out-Null
Copy-Item -Path (Join-Path $DownloadResources "*") -Destination $NuitkaDownloadCache -Recurse -Force

$PreviousNoIndex = $env:PIP_NO_INDEX
$PreviousFindLinks = $env:PIP_FIND_LINKS
$PreviousPythonPath = $env:PYTHONPATH
$BuiltFiles = @()
$LocationPushed = $false
try {
    Push-Location $ProjectRoot
    $LocationPushed = $true
    $env:PIP_NO_INDEX = "1"
    $env:PIP_FIND_LINKS = [IO.Path]::GetFullPath($Wheelhouse)

    Write-Host "[2/9] 生成多尺寸应用图标"
    & $Python (Join-Path $PSScriptRoot "create_icon.py")
    if ($LASTEXITCODE -ne 0) { throw "应用图标生成失败" }

    Write-Host "[3/9] 运行自动测试"
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest未通过" }

    Write-Host "[4/9] 运行Ruff"
    $Ruff = Join-Path (Split-Path -Parent $Python) "ruff.exe"
    & $Ruff check src tests packaging/create_icon.py packaging/nuitka_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Ruff未通过" }

    Write-Host "[5/9] 清理项目专用生成目录"
    Remove-ExactGeneratedDirectory $StageRoot (Join-Path $env:LOCALAPPDATA "ExamDeskNuitkaOfflineBuild100")
    Remove-ExactGeneratedDirectory $DistRoot (Join-Path $ProjectRoot "dist_nuitka")
    New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DeliveryRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "app.ico") -Destination $StageIcon -Force
    Copy-Item -LiteralPath $EntrySource -Destination $StageEntry -Force

    foreach ($Edition in $Editions) {
        $step = if ($Edition.Key -eq "admin") { "6/9" } else { "7/9" }
        Write-Host "[$step] 构建$($Edition.Label)"
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

        $env:PYTHONPATH = $EditionSourceRoot
        $EditionBuildRoot = Join-Path $BuildRoot $Edition.Key
        New-Item -ItemType Directory -Path $EditionBuildRoot -Force | Out-Null
        $NuitkaArgs = @(
            "--mode=standalone",
            "--zig",
            "--assume-yes-for-downloads",
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
        if ($LASTEXITCODE -ne 0) { throw "$($Edition.Label)Nuitka构建失败" }

        $CompiledRoot = Get-ChildItem -LiteralPath $EditionBuildRoot -Directory -Filter "*.dist" |
            Select-Object -First 1
        if (-not $CompiledRoot) {
            throw "未找到$($Edition.Label)Nuitka standalone输出目录"
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
        Copy-Item -LiteralPath $ZipPath -Destination $DeliveryRoot -Force
        $BuiltFiles += Join-Path $AppRoot $Edition.ExeName
        $BuiltFiles += $ZipPath
    }

    Write-Host "[8/9] 两个离线发行版本整理完成"
    Write-Host "[9/9] 输出SHA-256"
    foreach ($Path in $BuiltFiles) {
        $File = Get-Item -LiteralPath $Path
        $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Path
        Write-Host $File.FullName
        Write-Host "大小：$($File.Length)字节"
        Write-Host "SHA-256：$($Hash.Hash)"
    }
}
finally {
    if ($LocationPushed) {
        Pop-Location
    }
    Restore-EnvironmentVariable "PIP_NO_INDEX" $PreviousNoIndex
    Restore-EnvironmentVariable "PIP_FIND_LINKS" $PreviousFindLinks
    Restore-EnvironmentVariable "PYTHONPATH" $PreviousPythonPath
}

