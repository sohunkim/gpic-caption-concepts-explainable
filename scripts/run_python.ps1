$ErrorActionPreference = "Stop"

$ActualRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Root = $ActualRoot

if ($env:GPIC_RUNTIME_ROOT) {
    $Root = (Resolve-Path -LiteralPath $env:GPIC_RUNTIME_ROOT).Path
} elseif (-not ((Test-Path -LiteralPath (Join-Path $ActualRoot ".mamba\env\python.exe")) -and (Test-Path -LiteralPath (Join-Path $ActualRoot "src")))) {
    $AsciiRootCandidates = @(
        (Join-Path $env:USERPROFILE "Documents\Codex\gpic-explainable-link"),
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Codex\gpic-explainable-link")
    )
    foreach ($AsciiRoot in $AsciiRootCandidates) {
        $AsciiPython = Join-Path $AsciiRoot ".mamba\env\python.exe"
        $AsciiSrc = Join-Path $AsciiRoot "src"
        if ((Test-Path -LiteralPath $AsciiPython) -and (Test-Path -LiteralPath $AsciiSrc)) {
            $Root = (Resolve-Path -LiteralPath $AsciiRoot).Path
            break
        }
    }
}

$EnvPath = Join-Path $Root ".mamba\env"
$Python = Join-Path $EnvPath "python.exe"
$Src = Join-Path $Root "src"

if (-not (Test-Path $Python)) {
    throw "Project Python environment was not found. Run scripts\setup_env.ps1 first."
}

function Get-NormalizedPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Root $PathValue))
}

function Assert-RunPythonArgsAllowed {
    param([string[]]$PythonArgs)

    if ($PythonArgs.Count -eq 0) {
        throw "Direct interactive Python via scripts\run_python.ps1 is disabled. Use scripts\run_tests.ps1 for tests or scripts\run_script_with_timeout.py for scripts."
    }

    if ($PythonArgs.Count -ge 2 -and $PythonArgs[0] -eq "-m") {
        $Module = $PythonArgs[1]
        if ($Module -in @("unittest", "pytest")) {
            throw "Do not run scripts\run_python.ps1 -m $Module. Use scripts\run_tests.ps1 --timeout-seconds N instead."
        }
        return
    }

    if ($PythonArgs[0] -eq "-c") {
        return
    }

    if ($PythonArgs[0] -in @("-V", "--version")) {
        return
    }

    if ($PythonArgs[0].ToLowerInvariant().EndsWith(".py")) {
        $AllowedScripts = @(
            (Join-Path $Root "scripts\incident_gate.py"),
            (Join-Path $Root "scripts\run_background_job.py"),
            (Join-Path $Root "scripts\run_report_server_operation.py"),
            (Join-Path $Root "scripts\run_mlxp_bash.py"),
            (Join-Path $Root "scripts\run_mlxp_probe_bash.py"),
            (Join-Path $Root "scripts\run_script_with_timeout.py"),
            (Join-Path $Root "scripts\run_unittest_with_timeout.py"),
            (Join-Path $Root "scripts\run_pytest_with_timeout.py"),
            (Join-Path $Root "scripts\diagnose_test_runtime.py")
        ) | ForEach-Object { [System.IO.Path]::GetFullPath($_) }

        $RequestedScript = Get-NormalizedPath $PythonArgs[0]
        if ($RequestedScript -notin $AllowedScripts) {
            throw "Direct Python script execution via scripts\run_python.ps1 is disabled for '$($PythonArgs[0])'. Use scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds N -- <script> ..."
        }
    }
}

Assert-RunPythonArgsAllowed -PythonArgs $Args

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$Src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $Src
}

& $Python -B @Args
exit $LASTEXITCODE
