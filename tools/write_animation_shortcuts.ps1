param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
if ($plan.schema -ne 'cdesigner.animation-exchange-shortcut-plan/1' -or $plan.mode -ne 'write') {
    throw 'Use a verified write-mode animation entry plan.'
}
$entryRoot = [IO.Path]::GetFullPath($plan.output)
function Assert-RegularPath([string]$Path) {
    $probe = [IO.Path]::GetFullPath($Path)
    while ($probe) {
        if (Test-Path -LiteralPath $probe) {
            $item = Get-Item -LiteralPath $probe -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path was preserved: $probe" }
        }
        $parent = [IO.Path]::GetDirectoryName($probe)
        if ($parent -eq $probe) { break }
        $probe = $parent
    }
}
function Entry-Path([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($entryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Entry path escapes its output directory: $resolved"
    }
    Assert-RegularPath $resolved
    return $resolved
}
Assert-RegularPath $entryRoot
$ownerPath = Entry-Path $plan.owner_path
$ownerRaw = [IO.File]::ReadAllText($ownerPath)
$owner = $ownerRaw | ConvertFrom-Json -AsHashtable
if ($owner.schema -ne 'cdesigner.readable-animation-entries-owner/1') { throw 'Invalid entry ownership.' }
foreach ($source in $plan.source_metadata) {
    Assert-RegularPath $source.path
    if ((Get-FileHash -LiteralPath $source.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $source.sha256) {
        throw 'Source metadata changed. Generate a fresh entry plan.'
    }
}
foreach ($file in $plan.entry_files.GetEnumerator()) {
    $path = Entry-Path (Join-Path $entryRoot $file.Key)
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) {
        throw "Entry changed after planning: $path"
    }
}
$shell = New-Object -ComObject WScript.Shell
$verified = [Collections.Generic.List[object]]::new()
try {
    # Validate every planned destination before writing any shortcut.
    foreach ($entry in $plan.shortcuts) {
        $path = Entry-Path $entry.path
        if ([IO.Path]::GetExtension($path) -ne '.lnk' -or
            $path -ne (Entry-Path (Join-Path $entryRoot $entry.relative_path))) { throw 'Invalid shortcut path.' }
        Assert-RegularPath $entry.target
        if (-not (Test-Path -LiteralPath $entry.target -PathType Container)) { throw "Shortcut target unavailable: $($entry.target)" }
        if (Test-Path -LiteralPath $path) {
            $link = $shell.CreateShortcut($path)
            $expected = if ($Check) { $entry.target } else { $entry.expected_previous_target }
            if (-not $expected -or $link.TargetPath -ne $expected) { throw "Changed or unowned shortcut was preserved: $path" }
        } elseif ($Check) { throw "Shortcut is missing: $path" }
    }
    foreach ($entry in $plan.shortcuts) {
        $path = Entry-Path $entry.path
        if (-not $Check) {
            if ([IO.File]::ReadAllText($ownerPath) -ne $ownerRaw) { throw 'Entry ownership changed; rerun.' }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
            $temporary = Entry-Path (Join-Path ([IO.Path]::GetDirectoryName($path)) ('.entry-' + [Guid]::NewGuid().ToString('N') + '.lnk'))
            try {
                $link = $shell.CreateShortcut($temporary)
                $link.TargetPath = $entry.target
                $link.WorkingDirectory = $entry.target
                $link.Description = if ($entry.section -eq 'Prepared') { 'Prepared animation folder: ' + $entry.clip_name } else { 'Historical animation preparation: ' + $entry.clip_name }
                $link.Save()
                if ($shell.CreateShortcut($temporary).TargetPath -ne $entry.target) { throw 'Shortcut verification failed.' }
                # Recheck destination ownership immediately before publishing.
                Assert-RegularPath $path
                if (Test-Path -LiteralPath $path) {
                    if (-not $entry.expected_previous_target -or
                        $shell.CreateShortcut($path).TargetPath -ne $entry.expected_previous_target) {
                        throw "Shortcut changed during writing; it was preserved: $path"
                    }
                } elseif ($entry.expected_previous_target) { throw 'An owned shortcut disappeared during writing; rerun.' }
                if ([IO.File]::ReadAllText($ownerPath) -ne $ownerRaw -or
                    $owner.shortcuts[$entry.relative_path] -ne $(if ($entry.expected_previous_target) { $entry.expected_previous_target } else { $entry.target })) {
                    throw 'Shortcut ownership changed; rerun.'
                }
                # Both resolved paths are checked within the dedicated output directory.
                [IO.File]::Move($temporary, $path, $true)
                $owner.shortcuts[$entry.relative_path] = $entry.target
                $ownerRaw = ($owner | ConvertTo-Json -Depth 20) + "`n"
                [IO.File]::WriteAllText($ownerPath, $ownerRaw, [Text.UTF8Encoding]::new($false))
            } finally {
                if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
            }
        }
        if ($shell.CreateShortcut($path).TargetPath -ne $entry.target -or $owner.shortcuts[$entry.relative_path] -ne $entry.target) {
            throw "Shortcut readback failed: $path"
        }
        $verified.Add(@{path=$path; target=$entry.target; section=$entry.section})
    }
} finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null }
@{passed=$true; check_only=[bool]$Check; shortcuts=$verified} | ConvertTo-Json -Depth 8
