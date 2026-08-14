param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Text-FromCodePoints([int[]]$CodePoints) {
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

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

$adminEntryText = Text-FromCodePoints @(0x4E3B, 0x7BA1, 0x7406, 0x5458, 0x767B, 0x5F55)
$loginDialogText = Text-FromCodePoints @(0x7BA1, 0x7406, 0x5458, 0x767B, 0x5F55)
$loginText = Text-FromCodePoints @(0x767B, 0x5F55)
$adminCenterText = Text-FromCodePoints @(0x7BA1, 0x7406, 0x4E2D, 0x5FC3)
$questionBankText = Text-FromCodePoints @(0x9898, 0x5E93)
$tagsText = Text-FromCodePoints @(0x6807, 0x7B7E)
$testRoot = Join-Path $env:TEMP ("ExamDesk100LoginSmoke_" + [guid]::NewGuid().ToString("N"))
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
        $window = Find-Until $desktop ([System.Windows.Automation.TreeScope]::Children) $processCondition
        if ($null -eq $window) {
            throw "Main window was not found"
        }

        $buttonType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::Button)
        $adminName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $adminEntryText
        $adminButton = Find-Until `
            $desktop `
            ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition (And-Condition $buttonType $adminName))
        if ($null -eq $adminButton) {
            throw "Supervisor login button was not found"
        }
        $adminButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $dialogName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $loginDialogText
        $dialog = Find-Until `
            $desktop `
            ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition $dialogName)
        if ($null -eq $dialog) {
            throw "Administrator login dialog was not found"
        }

        $editType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::Edit)
        $edits = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editType)
        if ($edits.Count -lt 2) {
            throw "Unexpected login field count: $($edits.Count)"
        }
        $edits.Item(0).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue("smoke-admin")
        $edits.Item(1).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue("smoke-pass")

        $loginName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $loginText
        $loginButton = $dialog.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            (And-Condition $buttonType $loginName)
        )
        if ($null -eq $loginButton) {
            throw "Login button was not found"
        }
        $loginButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $centerName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $adminCenterText
        $center = Find-Until `
            $desktop `
            ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition $centerName)
        if ($null -eq $center) {
            throw "Admin center did not open after login"
        }

        $checkBoxType = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::ControlTypeProperty) `
            ([System.Windows.Automation.ControlType]::CheckBox)
        $questionBankName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $questionBankText
        $questionBankButton = Find-Until `
            $desktop `
            ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition (And-Condition $checkBoxType $questionBankName))
        if ($null -eq $questionBankButton) {
            throw "Question bank navigation control was not found"
        }
        $questionBankButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

        $tagsName = Property-Condition `
            ([System.Windows.Automation.AutomationElement]::NameProperty) `
            $tagsText
        $tagsHeader = Find-Until `
            $desktop `
            ([System.Windows.Automation.TreeScope]::Descendants) `
            (And-Condition $processCondition $tagsName)
        if ($null -eq $tagsHeader) {
            $controls = $desktop.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $processCondition
            )
            $visibleNames = @(
                $controls | ForEach-Object {
                    if ($_.Current.Name) {
                        "$($_.Current.ControlType.ProgrammaticName):$($_.Current.Name)"
                    }
                }
            )
            throw "Question bank tags column was not found. Controls: $($visibleNames -join ' | ')"
        }
        Write-Output "EXE_ADMIN_LOGIN_AND_TAGS_SMOKE_OK"
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
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
    if ($resolvedRoot.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    }
}
