# Read-only native Windows environment checks, including when Python is absent.
$checks = @()
$python = $null
$isWindowsNative = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$shellVersion = $PSVersionTable.PSVersion
$supportedShell = ($shellVersion.Major -eq 5 -and $shellVersion.Minor -ge 1) -or
    $shellVersion.Major -eq 7

function Get-PythonCandidates {
    Get-Command -Name 'python.exe', 'python3.exe', 'python3.*.exe' -CommandType Application -All `
        -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }

    # PEP 514 registrations cover python.org and other CPython distributions,
    # including ones which are installed but absent from this shell's PATH.
    foreach ($root in @(
        'Registry::HKEY_CURRENT_USER\SOFTWARE\Python',
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Python',
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python'
    )) {
        foreach ($vendor in Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue) {
            foreach ($tag in Get-ChildItem -LiteralPath $vendor.PSPath -ErrorAction SilentlyContinue) {
                $registration = Get-Item -LiteralPath "$($tag.PSPath)\InstallPath" -ErrorAction SilentlyContinue
                if ($null -eq $registration) { continue }
                $executable = $registration.GetValue('ExecutablePath')
                $directory = $registration.GetValue('')
                if ($executable) { $executable }
                elseif ($directory) { Join-Path $directory 'python.exe' -ErrorAction SilentlyContinue }
            }
        }
    }

    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", $env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:SystemDrive\")) {
        if ($root -notmatch '^[A-Za-z]:\\') { continue }
        foreach ($install in Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue) {
            Join-Path $install.FullName 'python.exe'
        }
    }
}

if ($isWindowsNative) {
    $checks += @{ name = 'system'; status = 'passed'; detail = 'Native Windows.' }
    if ($supportedShell) {
        $checks += @{ name = 'terminal'; status = 'passed'; detail = "PowerShell $shellVersion." }
    } else {
        $checks += @{ name = 'terminal'; status = 'incompatible'; detail = 'Use Windows PowerShell 5.1 or PowerShell 7.' }
    }

    # Use only real Python executables. Store aliases can open a store or start
    # provisioning, and version-manager shims can run arbitrary hooks.
    $probe = @'
import json, os, sys
result = dict(path=os.path.realpath(sys.executable), version='.'.join(map(str, sys.version_info[:3])))
if sys.platform != 'win32' or sys.implementation.name != 'cpython' or sys.version_info < (3, 12):
    result['implementation'] = sys.implementation.name
    print(json.dumps(result))
    raise SystemExit(3)
print(json.dumps(result))
'@
    $incompatible = $false
    $observed = $null
    $failed = $false
    foreach ($candidate in Get-PythonCandidates | Select-Object -Unique) {
        if ($candidate -notmatch '^[A-Za-z]:[\\/].*\.exe$' -or $candidate -match '[\\/](WindowsApps|shims)[\\/]') { continue }
        $file = Get-Item -LiteralPath $candidate -ErrorAction SilentlyContinue
        if ($null -eq $file -or $file.PSIsContainer -or $file.VersionInfo.ProductName -notmatch '^Python(?:\s|$)') { continue }

        try {
            $result = & $file.FullName -I -S -B -c $probe 2>$null
            if ($LASTEXITCODE -eq 3) {
                $incompatible = $true
                if ($result) { $observed = $result | ConvertFrom-Json -ErrorAction Stop }
                continue
            }
            if ($LASTEXITCODE -ne 0) { $failed = $true; continue }
            $detected = $result | ConvertFrom-Json -ErrorAction Stop
            if ($detected.path -notmatch '^[A-Za-z]:[\\/]' -or $detected.version -notmatch '^\d+\.\d+\.\d+$') {
                $failed = $true
                continue
            }
            $python = $detected
            break
        } catch {
            $failed = $true
        }
    }

    if ($null -ne $python) {
        $checks += @{ name = 'python'; status = 'passed'; detail = "CPython $($python.version)." }
    } elseif ($incompatible) {
        $check = @{ name = 'python'; status = 'incompatible'; detail = 'No discovered interpreter met the CPython 3.12+ requirement.' }
        if ($null -ne $observed) { $check.observed = $observed }
        $checks += $check
    } elseif ($failed) {
        $checks += @{ name = 'python'; status = 'blocked'; detail = 'A Python executable was found but could not be verified.' }
    } else {
        $checks += @{ name = 'python'; status = 'missing'; detail = 'No installed CPython executable was found in PATH, Python registrations, or common install directories.' }
    }
} else {
    $checks += @{ name = 'system'; status = 'incompatible'; detail = 'This entry point requires native Windows.' }
    $checks += @{ name = 'terminal'; status = 'pending'; detail = 'Check on the supported operating system.' }
    $checks += @{ name = 'python'; status = 'pending'; detail = 'Check on the supported operating system.' }
}

$blocked = $null -ne ($checks | Where-Object { $_.status -ne 'passed' } | Select-Object -First 1)
if ($blocked) {
    $checks += @{ name = 'volumes'; status = 'pending'; detail = 'Complete the native environment checks first.' }
    $checks += @{ name = 'git'; status = 'pending'; detail = 'Complete the native environment checks first.' }
}

# Keep stdout unambiguous even on Windows PowerShell's legacy code pages.
$json = @{ checks = $checks; python = $python } | ConvertTo-Json -Depth 5 -Compress
[regex]::Replace($json, '[^\u0000-\u007f]', { param($match) '\u{0:x4}' -f [int][char]$match.Value })
if ($blocked) { exit 1 }
exit 0
