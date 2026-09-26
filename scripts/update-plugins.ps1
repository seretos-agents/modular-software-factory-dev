#Requires -Version 5.1
<#
    Installs/updates the Claude Code plugins that are enabled in each
    sub-project's .claude/settings.json.

    Sub-projects are the repos listed in workspace.json (the sibling
    checkouts under this workspace). For each one, every plugin id found
    under "enabledPlugins" (value == true) is installed and then updated
    by running `claude plugin install` / `claude plugin update` with the
    sub-project directory as the current directory.

    Throws (non-zero exit code) if any plugin fails to install or update.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceManifestPath = Join-Path $repoRoot 'workspace.json'

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    throw "The 'claude' CLI was not found on PATH."
}

if (-not (Test-Path $workspaceManifestPath)) {
    throw "workspace.json not found at '$workspaceManifestPath'."
}

$workspace = Get-Content -Raw -Path $workspaceManifestPath | ConvertFrom-Json
$failures = [System.Collections.Generic.List[object]]::new()

function Invoke-ClaudePluginCommand {
    param(
        [ValidateSet('install', 'update')]
        [string]$Action,
        [string]$PluginId
    )

    $cliArgs = @('plugin', $Action, $PluginId, '-y', '--scope', 'project', '--json')
    $stdout = & claude @cliArgs 2>$null
    $exitCode = $LASTEXITCODE

    $result = $null
    if ($stdout) {
        try { $result = ($stdout | Select-Object -Last 1) | ConvertFrom-Json } catch {}
    }

    $ok = ($exitCode -eq 0) -and (-not $result -or $result.outcome -ne 'failed')
    $message = if ($result) { $result.message } elseif ($stdout) { $stdout -join "`n" } else { "claude exited with code $exitCode and no output" }

    [PSCustomObject]@{
        Ok      = $ok
        Message = $message
    }
}

foreach ($repo in $workspace.repos) {
    $repoPath = Join-Path $repoRoot $repo.path
    $settingsPath = Join-Path (Join-Path $repoPath '.claude') 'settings.json'

    if (-not (Test-Path $repoPath)) {
        Write-Host "[skip] $($repo.name): sub-project not checked out at '$repoPath'" -ForegroundColor DarkGray
        continue
    }

    if (-not (Test-Path $settingsPath)) {
        Write-Host "[skip] $($repo.name): no .claude/settings.json" -ForegroundColor DarkGray
        continue
    }

    $settings = Get-Content -Raw -Path $settingsPath | ConvertFrom-Json
    $enabledPluginIds = @()
    if ($settings.enabledPlugins) {
        $enabledPluginIds = $settings.enabledPlugins.PSObject.Properties |
            Where-Object { $_.Value -eq $true } |
            Select-Object -ExpandProperty Name
    }

    if (-not $enabledPluginIds) {
        Write-Host "[skip] $($repo.name): no enabled plugins in settings.json" -ForegroundColor DarkGray
        continue
    }

    Push-Location $repoPath
    try {
        foreach ($pluginId in $enabledPluginIds) {
            Write-Host "[$($repo.name)] installing $pluginId ..."
            $installResult = Invoke-ClaudePluginCommand -Action install -PluginId $pluginId
            if (-not $installResult.Ok) {
                $failures.Add([PSCustomObject]@{ Repo = $repo.name; Plugin = $pluginId; Action = 'install'; Message = $installResult.Message })
                Write-Host "  FAILED install: $($installResult.Message)" -ForegroundColor Red
                continue
            }

            Write-Host "[$($repo.name)] updating $pluginId ..."
            $updateResult = Invoke-ClaudePluginCommand -Action update -PluginId $pluginId
            if (-not $updateResult.Ok) {
                $failures.Add([PSCustomObject]@{ Repo = $repo.name; Plugin = $pluginId; Action = 'update'; Message = $updateResult.Message })
                Write-Host "  FAILED update: $($updateResult.Message)" -ForegroundColor Red
                continue
            }

            Write-Host "  OK: $($updateResult.Message)" -ForegroundColor Green
        }
    }
    finally {
        Pop-Location
    }
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "==== $($failures.Count) plugin operation(s) failed ====" -ForegroundColor Red
    foreach ($failure in $failures) {
        Write-Host "  [$($failure.Repo)] $($failure.Action) $($failure.Plugin): $($failure.Message)" -ForegroundColor Red
    }
    throw "$($failures.Count) plugin install/update operation(s) failed. See log above."
}

Write-Host ""
Write-Host "All enabled plugins installed/updated successfully." -ForegroundColor Green
