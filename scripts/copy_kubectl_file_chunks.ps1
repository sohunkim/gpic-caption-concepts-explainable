param(
    [Parameter(Mandatory = $true)]
    [string]$Namespace,

    [Parameter(Mandatory = $true)]
    [string]$Pod,

    [Parameter(Mandatory = $true)]
    [string]$RemotePath,

    [Parameter(Mandatory = $true)]
    [string]$LocalPath,

    [string]$RemoteChunkDir = "",

    [int]$ChunkMiB = 512,

    [int]$Retries = 3
)

$ErrorActionPreference = "Stop"

function Convert-ToWslPath {
    param([Parameter(Mandatory = $true)][string]$WindowsPath)
    $full = [System.IO.Path]::GetFullPath($WindowsPath)
    if ($full -notmatch "^([A-Za-z]):\\(.*)$") {
        throw "Only absolute Windows drive paths are supported: $WindowsPath"
    }
    $drive = $matches[1].ToLowerInvariant()
    $rest = $matches[2].Replace("\", "/")
    return "/mnt/$drive/$rest"
}

function Invoke-WslBash {
    param([Parameter(Mandatory = $true)][string]$Command)
    & C:\Windows\System32\wsl.exe -e bash -lc $Command
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

function Get-RemoteShellLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Get-FileSha256Lower {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

if ($ChunkMiB -lt 16) {
    throw "ChunkMiB must be at least 16."
}
if ($Retries -lt 1) {
    throw "Retries must be at least 1."
}

$localFull = [System.IO.Path]::GetFullPath($LocalPath)
$localDir = [System.IO.Path]::GetDirectoryName($localFull)
if (-not $localDir) {
    throw "LocalPath must include a directory: $LocalPath"
}
New-Item -ItemType Directory -Force -Path $localDir | Out-Null

$baseName = [System.IO.Path]::GetFileName($localFull)
$chunkLocalDir = Join-Path $localDir "${baseName}.chunks"
New-Item -ItemType Directory -Force -Path $chunkLocalDir | Out-Null

if (-not $RemoteChunkDir) {
    $safeBase = $baseName -replace "[^A-Za-z0-9._-]", "_"
    $RemoteChunkDir = "/mnt/nvme/kubectl_chunked_copy/${safeBase}"
}

$remotePathQ = Get-RemoteShellLiteral $RemotePath
$remoteChunkDirQ = Get-RemoteShellLiteral $RemoteChunkDir
$chunkBytes = [int64]$ChunkMiB * 1024 * 1024

Write-Host "Preparing remote chunks..."
$prepare = @"
set -euo pipefail
mkdir -p $remoteChunkDirQ
if [ ! -s $remoteChunkDirQ/file.sha256 ] || [ ! -s $remoteChunkDirQ/chunks.sha256 ]; then
  rm -f $remoteChunkDirQ/part_*
  split -b $chunkBytes -d --additional-suffix=.part $remotePathQ $remoteChunkDirQ/part_
  (cd $remoteChunkDirQ && sha256sum part_* > chunks.sha256)
  sha256sum $remotePathQ > $remoteChunkDirQ/file.sha256
  stat -c '%s' $remotePathQ > $remoteChunkDirQ/file.size
fi
cat $remoteChunkDirQ/file.sha256
cat $remoteChunkDirQ/file.size
wc -l $remoteChunkDirQ/chunks.sha256
"@
Invoke-WslBash "kubectl -n $Namespace exec $Pod -- bash -lc $(Get-RemoteShellLiteral $prepare)"

$chunksShaLocal = Join-Path $chunkLocalDir "chunks.sha256"
$fileShaLocal = Join-Path $chunkLocalDir "file.sha256"
$fileSizeLocal = Join-Path $chunkLocalDir "file.size"

Invoke-WslBash "kubectl -n $Namespace cp ${Pod}:${RemoteChunkDir}/chunks.sha256 $(Convert-ToWslPath $chunksShaLocal)"
Invoke-WslBash "kubectl -n $Namespace cp ${Pod}:${RemoteChunkDir}/file.sha256 $(Convert-ToWslPath $fileShaLocal)"
Invoke-WslBash "kubectl -n $Namespace cp ${Pod}:${RemoteChunkDir}/file.size $(Convert-ToWslPath $fileSizeLocal)"

$expectedFileSha = ((Get-Content -Raw -LiteralPath $fileShaLocal -Encoding UTF8).Trim() -split "\s+")[0].ToLowerInvariant()
$expectedSize = [int64]((Get-Content -Raw -LiteralPath $fileSizeLocal -Encoding UTF8).Trim() -split "\s+")[0]
$chunkRows = Get-Content -LiteralPath $chunksShaLocal -Encoding UTF8 | Where-Object { $_.Trim() }

$index = 0
foreach ($row in $chunkRows) {
    $index += 1
    $parts = $row -split "\s+"
    $expectedChunkSha = $parts[0].ToLowerInvariant()
    $chunkName = $parts[-1]
    $localChunk = Join-Path $chunkLocalDir $chunkName

    if (Test-Path -LiteralPath $localChunk) {
        $existingSha = Get-FileSha256Lower $localChunk
        if ($existingSha -eq $expectedChunkSha) {
            Write-Host "Chunk $index/$($chunkRows.Count) already verified: $chunkName"
            continue
        }
        Write-Host "Chunk $index/$($chunkRows.Count) exists but SHA mismatched; recopying: $chunkName"
        Remove-Item -LiteralPath $localChunk -Force
    }

    $ok = $false
    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        Write-Host "Copying chunk $index/$($chunkRows.Count), attempt ${attempt}: $chunkName"
        try {
            Invoke-WslBash "kubectl -n $Namespace cp ${Pod}:${RemoteChunkDir}/${chunkName} $(Convert-ToWslPath $localChunk)"
            $actualSha = Get-FileSha256Lower $localChunk
            if ($actualSha -ne $expectedChunkSha) {
                throw "chunk SHA mismatch for ${chunkName}: expected $expectedChunkSha got $actualSha"
            }
            $ok = $true
            break
        }
        catch {
            Write-Warning $_
            if (Test-Path -LiteralPath $localChunk) {
                Remove-Item -LiteralPath $localChunk -Force
            }
            if ($attempt -eq $Retries) {
                throw
            }
            Start-Sleep -Seconds ([Math]::Min(10 * $attempt, 30))
        }
    }
    if (-not $ok) {
        throw "failed to copy verified chunk: $chunkName"
    }
}

$tmpPath = "$localFull.assembling"
if (Test-Path -LiteralPath $tmpPath) {
    Remove-Item -LiteralPath $tmpPath -Force
}

Write-Host "Assembling local file..."
$out = [System.IO.File]::Open($tmpPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    foreach ($row in $chunkRows) {
        $chunkName = (($row -split "\s+")[-1])
        $localChunk = Join-Path $chunkLocalDir $chunkName
        $in = [System.IO.File]::Open($localChunk, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {
            $in.CopyTo($out)
        }
        finally {
            $in.Dispose()
        }
    }
}
finally {
    $out.Dispose()
}

$actualSize = (Get-Item -LiteralPath $tmpPath).Length
if ($actualSize -ne $expectedSize) {
    throw "assembled size mismatch: expected $expectedSize got $actualSize"
}
$actualFileSha = Get-FileSha256Lower $tmpPath
if ($actualFileSha -ne $expectedFileSha) {
    throw "assembled SHA mismatch: expected $expectedFileSha got $actualFileSha"
}

Move-Item -LiteralPath $tmpPath -Destination $localFull -Force
Write-Host "OK $localFull"
Write-Host "sha256=$actualFileSha"
Write-Host "bytes=$actualSize"
