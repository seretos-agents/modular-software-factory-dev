#Requires -Version 5.1
<#
    Updates every sub-project in workspace.json to the latest `main` and then
    reports which ones now have commits on main that are newer than their
    last released version.

    Why date comparison instead of `git merge-base`/ahead-behind:
    Several repos in this ecosystem are released by force-pushing an orphan
    `release` branch and tagging a commit on it (see e.g. plugins/*/AGENTS.md:
    "main and release share no history -- never merge between them"). A
    release tag's commit is therefore NOT an ancestor of main, so counting
    "commits between tag and main" via merge-base would either error out or
    silently return nonsense for those repos. Comparing commit *dates*
    instead works uniformly whether or not the tag shares history with main.

    Repos with no tags at all (e.g. the marketplace repos, which aren't
    versioned/released) are reported in their own "no tags" bucket rather
    than being counted as "newer than last version".

    Repos with local changes, a diverged local branch, or a failed
    fetch/checkout are left untouched and reported as skipped -- this script
    never resets or discards local work.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceManifestPath = Join-Path $repoRoot 'workspace.json'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git was not found on PATH."
}

if (-not (Test-Path $workspaceManifestPath)) {
    throw "workspace.json not found at '$workspaceManifestPath'."
}

$workspace = Get-Content -Raw -Path $workspaceManifestPath | ConvertFrom-Json

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments)][string[]]$GitArgs)
    $output = & git @GitArgs 2>&1
    [PSCustomObject]@{
        ExitCode = $LASTEXITCODE
        Output   = ($output -join "`n")
    }
}

$results = [System.Collections.Generic.List[object]]::new()

foreach ($repo in $workspace.repos) {
    $repoPath = Join-Path $repoRoot $repo.path
    $entry = [ordered]@{
        Name   = $repo.name
        State  = 'unknown'
        Detail = ''
    }

    if (-not (Test-Path (Join-Path $repoPath '.git'))) {
        $entry.State = 'not-checked-out'
        $entry.Detail = "no .git directory at '$repoPath'"
        $results.Add([PSCustomObject]$entry)
        continue
    }

    Push-Location $repoPath
    try {
        $dirty = Invoke-Git status --porcelain
        if ($dirty.Output.Trim()) {
            $entry.State = 'skipped-dirty'
            $entry.Detail = 'uncommitted changes present -- left untouched'
            $results.Add([PSCustomObject]$entry)
            continue
        }

        Write-Host "[$($repo.name)] fetching ..."
        $fetch = Invoke-Git fetch origin --tags --prune
        if ($fetch.ExitCode -ne 0) {
            $entry.State = 'skipped-fetch-failed'
            $entry.Detail = $fetch.Output
            $results.Add([PSCustomObject]$entry)
            continue
        }

        $branch = (Invoke-Git rev-parse --abbrev-ref HEAD).Output.Trim()
        if ($branch -ne $repo.branch) {
            $checkout = Invoke-Git checkout $repo.branch
            if ($checkout.ExitCode -ne 0) {
                $entry.State = 'skipped-checkout-failed'
                $entry.Detail = $checkout.Output
                $results.Add([PSCustomObject]$entry)
                continue
            }
        }

        $ff = Invoke-Git merge --ff-only "origin/$($repo.branch)"
        if ($ff.ExitCode -ne 0) {
            $entry.State = 'skipped-diverged'
            $entry.Detail = "local $($repo.branch) has diverged from origin/$($repo.branch) -- not fast-forwardable, left untouched"
            $results.Add([PSCustomObject]$entry)
            continue
        }

        $headDateRaw = (Invoke-Git log -1 '--format=%cI' 'HEAD').Output.Trim()
        $headSha = (Invoke-Git rev-parse --short HEAD).Output.Trim()
        $headDate = [DateTimeOffset]::Parse($headDateRaw)

        $latestTagLine = (Invoke-Git for-each-ref '--sort=-creatordate' '--count=1' '--format=%(refname:short)|%(creatordate:iso-strict)' 'refs/tags').Output.Trim()

        if (-not $latestTagLine) {
            $entry.State = 'no-tags'
            $entry.Detail = "main @ $headSha ($($headDate.ToString('yyyy-MM-dd'))) -- no release tags found, treated as unversioned"
            $results.Add([PSCustomObject]$entry)
            continue
        }

        $tagParts = $latestTagLine -split '\|', 2
        $tagName = $tagParts[0]
        $tagDate = [DateTimeOffset]::Parse($tagParts[1])

        if ($headDate -gt $tagDate) {
            $sinceCommits = (Invoke-Git log '--oneline' "--since=$($tagDate.ToString('o'))" 'HEAD').Output.Trim()
            $commitCount = if ($sinceCommits) { ($sinceCommits -split "`n").Count } else { 0 }
            $entry.State = 'newer-than-last-version'
            $entry.Detail = "main @ $headSha is newer than last tag '$tagName' ($($tagDate.ToString('yyyy-MM-dd'))) -- ~$commitCount commit(s) since (by date; tag may be on an unrelated release branch)"
        }
        else {
            $entry.State = 'up-to-date'
            $entry.Detail = "main matches last released tag '$tagName' ($($tagDate.ToString('yyyy-MM-dd')))"
        }
        $results.Add([PSCustomObject]$entry)
    }
    finally {
        Pop-Location
    }
}

function Write-Section {
    param([string]$Title, [array]$Items, [string]$Color)
    if (-not $Items -or $Items.Count -eq 0) { return }
    Write-Host ""
    Write-Host "==== $Title ($($Items.Count)) ====" -ForegroundColor $Color
    foreach ($item in $Items) {
        Write-Host "  $($item.Name): $($item.Detail)"
    }
}

$newer   = $results | Where-Object { $_.State -eq 'newer-than-last-version' }
$upToDate = $results | Where-Object { $_.State -eq 'up-to-date' }
$noTags  = $results | Where-Object { $_.State -eq 'no-tags' }
$skipped = $results | Where-Object { $_.State -like 'skipped-*' -or $_.State -eq 'not-checked-out' }

Write-Section -Title 'Newer on main than last released version' -Items $newer -Color Yellow
Write-Section -Title 'Up to date with last released version' -Items $upToDate -Color Green
Write-Section -Title 'No release tags (unversioned)' -Items $noTags -Color DarkGray
Write-Section -Title 'Skipped (need manual attention)' -Items $skipped -Color Red

Write-Host ""
if ($newer.Count -gt 0) {
    Write-Host "$($newer.Count) repo(s) have unreleased changes on main:" -ForegroundColor Yellow
    $newer | ForEach-Object { Write-Host "  - $($_.Name)" }
}
else {
    Write-Host "No repo has unreleased changes on main." -ForegroundColor Green
}
