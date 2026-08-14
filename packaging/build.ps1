$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"

& $Python (Join-Path $PSScriptRoot "create_icon.py")
& $Python -m pytest
& (Join-Path $ProjectRoot ".venv311\Scripts\ruff.exe") check src tests
& (Join-Path $ProjectRoot ".venv311\Scripts\pyinstaller.exe") `
    --noconfirm `
    --clean `
    (Join-Path $PSScriptRoot "examdesk.spec")

$DistRoot = (Get-ChildItem (Join-Path $ProjectRoot "dist") -Directory | Select-Object -First 1).FullName
$TemplateRoot = Join-Path $DistRoot "templates"
New-Item -ItemType Directory -Path $TemplateRoot -Force | Out-Null
$Guide = Get-ChildItem (Join-Path $ProjectRoot "docs") -Filter "*.txt" | Select-Object -First 1
$Workbook = Get-ChildItem (Join-Path $ProjectRoot "templates") -Filter "*.xlsx" | Select-Object -First 1
Copy-Item $Guide.FullName $DistRoot -Force
Copy-Item $Workbook.FullName $TemplateRoot -Force
