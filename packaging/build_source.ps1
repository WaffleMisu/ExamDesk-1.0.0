$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DeliveryRoot = Join-Path $ProjectRoot "release"
$StageRoot = Join-Path $env:LOCALAPPDATA "ExamDeskSourcePackage100"
$PackageRoot = Join-Path $StageRoot "ExamDesk_1.0.0_Python源码"
$ZipPath = Join-Path $DeliveryRoot "ExamDesk_1.0.0_Python源码.zip"

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

Remove-ExactGeneratedDirectory $StageRoot (Join-Path $env:LOCALAPPDATA "ExamDeskSourcePackage100")
New-Item -ItemType Directory -Path $PackageRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DeliveryRoot -Force | Out-Null

foreach ($directory in @("src", "tests", "packaging", "templates", "docs", "licenses", ".github")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $directory) -Destination $PackageRoot -Recurse -Force
}
foreach ($file in @(
    "pyproject.toml", "README.md", ".gitignore", "一键重新打包.cmd",
    "LICENSE", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md"
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $file) -Destination $PackageRoot -Force
}

$resolvedStage = [IO.Path]::GetFullPath($StageRoot).TrimEnd('\') + '\'
$generatedDirectories = Get-ChildItem -LiteralPath $PackageRoot -Directory -Recurse | Where-Object {
    $_.Name -in @("__pycache__", ".pytest_cache") -or $_.Name.EndsWith(".egg-info")
}
foreach ($directory in $generatedDirectories) {
    $resolved = [IO.Path]::GetFullPath($directory.FullName)
    if (-not $resolved.StartsWith($resolvedStage, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理打包目录以外的路径：$resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ZipPath -CompressionLevel Optimal -Force
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath
$file = Get-Item -LiteralPath $ZipPath
Write-Host $file.FullName
Write-Host "大小：$($file.Length) 字节"
Write-Host "SHA-256：$($hash.Hash)"


