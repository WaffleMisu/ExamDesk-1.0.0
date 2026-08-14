param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Property-Condition($Property, $Value) {
    return New-Object System.Windows.Automation.PropertyCondition($Property, $Value)
}

function And-Condition($First, $Second) {
    return New-Object System.Windows.Automation.AndCondition($First, $Second)
}

function Find-Until($Root, $Scope, $Condition, [int]$Attempts = 100) {
    for ($index = 0; $index -lt $Attempts; $index++) {
        $element = $Root.FindFirst($Scope, $Condition)
        if ($null -ne $element) {
            return $element
        }
        Start-Sleep -Milliseconds 100
    }
    return $null
}

$testRoot = Join-Path $env:TEMP ("ExamDesk100MaintenanceSmoke_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$env:EXAMDESK_SMOKE_ROOT = $testRoot

try {
    $setupCode = @'
import os
from pathlib import Path
from examdesk.paths import AppPaths
from examdesk.security.passwords import hash_secret
from examdesk.ui import ApplicationContext

context = ApplicationContext.create(AppPaths.from_root(Path(os.environ["EXAMDESK_SMOKE_ROOT"])))
context.administrators.create_first_admin(
    "smoke-admin",
    "smoke-pass",
    hash_secret("RECOVERY").encode(),
)
'@
    $setupPath = Join-Path $testRoot "setup_smoke_data.py"
    [IO.File]::WriteAllText($setupPath, $setupCode, (New-Object Text.UTF8Encoding($false)))
    & $PythonPath $setupPath
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to prepare isolated smoke-test data"
    }

    $process = Start-Process -FilePath $ExePath -ArgumentList @("--data-root", $testRoot) -PassThru
    try {
        $desktop = [System.Windows.Automation.AutomationElement]::RootElement
        $processCondition = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ProcessIdProperty) `
            $process.Id
        $buttonType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::Button)
        $checkBoxType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::CheckBox)

        $adminButton = Find-Until $desktop ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition (And-Condition $buttonType `
                (Property-Condition ([System.Windows.Automation.AutomationElement]::NameProperty) "主管理员登录")))
        if ($null -eq $adminButton) { throw "Supervisor login button was not found" }
        $adminButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $dialog = Find-Until $desktop ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition `
                (Property-Condition ([System.Windows.Automation.AutomationElement]::NameProperty) "管理员登录"))
        if ($null -eq $dialog) { throw "Administrator login dialog was not found" }
        $editType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::Edit)
        $edits = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editType)
        $edits.Item(0).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue("smoke-admin")
        $edits.Item(1).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue("smoke-pass")
        $loginButton = $dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants, `
            (And-Condition $buttonType `
                (Property-Condition ([System.Windows.Automation.AutomationElement]::NameProperty) "登录")))
        $loginButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $maintenanceButton = Find-Until $desktop ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition (And-Condition $checkBoxType `
                (Property-Condition ([System.Windows.Automation.AutomationElement]::NameProperty) "系统维护")))
        if ($null -eq $maintenanceButton) { throw "System maintenance navigation was not found" }
        $maintenanceButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $removeButton = Find-Until $desktop ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition (And-Condition $buttonType `
                (Property-Condition ([System.Windows.Automation.AutomationElement]::NameProperty) "移除副管理员")))
        if ($null -eq $removeButton) { throw "Remove administrator button was not found" }
        Write-Output "EXE_SYSTEM_MAINTENANCE_REMOVE_ADMIN_SMOKE_OK"
    }
    finally {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
    }
}
finally {
    Remove-Item Env:\EXAMDESK_SMOKE_ROOT -ErrorAction SilentlyContinue
    $resolvedRoot = [IO.Path]::GetFullPath($testRoot)
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd([char]92) + [char]92
    if ($resolvedRoot.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    }
}
