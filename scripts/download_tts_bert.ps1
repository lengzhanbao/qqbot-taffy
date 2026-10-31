param(
    [string]$SourceRoot,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $PSScriptRoot "..\..\Bert-VITS2-master"
}
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
$manifestPath = Join-Path $PSScriptRoot "tts_bert_manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$v200Path = Join-Path $SourceRoot "oldVersion\V200\text\chinese_bert.py"
$target = Join-Path $SourceRoot "bert\chinese-roberta-wwm-ext-large"

if (-not (Test-Path -LiteralPath $v200Path -PathType Leaf)) {
    throw "Bert-VITS2 V200 source not found at '$SourceRoot'. Clone Bert-VITS2 there first or pass -SourceRoot."
}

if ($VerifyOnly) {
    if (-not (Test-Path -LiteralPath $target -PathType Container)) {
        throw "BERT directory not found: '$target'"
    }
} else {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}

function Test-ManifestHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Entry
    )

    $item = Get-Item -LiteralPath $Path
    if ($item.Length -eq $Entry.size) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
        if ($actual -eq $Entry.sha256) {
            return $true
        }
    }

    if (-not $Entry.allow_trailing_lf -or $item.Length -ne ($Entry.size + 1)) {
        return $false
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes[$bytes.Length - 1] -ne 10) {
        return $false
    }
    $normalized = [System.Array]::CreateInstance([byte], $bytes.Length - 1)
    [System.Array]::Copy($bytes, 0, $normalized, 0, $normalized.Length)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $actual = [System.BitConverter]::ToString($sha.ComputeHash($normalized)).Replace("-", "")
    } finally {
        $sha.Dispose()
    }
    return $actual -eq $Entry.sha256
}

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $VerifyOnly -and -not $curl) {
    throw "curl.exe is required. Use a recent Windows installation or install curl, then rerun."
}

foreach ($entry in $manifest.files) {
    $destination = Join-Path $target $entry.name
    $partial = "$destination.partial"

    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        if (Test-ManifestHash -Path $destination -Entry $entry) {
            Write-Host "Verified existing $($entry.name)"
            continue
        }
        throw "Existing file does not match pinned SHA-256; it was left unchanged: '$destination'"
    }

    if ($VerifyOnly) {
        throw "Required file is missing: '$destination'"
    }

    if (Test-Path -LiteralPath $partial -PathType Leaf) {
        $partialLength = (Get-Item -LiteralPath $partial).Length
        if ($partialLength -gt $entry.size) {
            throw "Partial file is larger than expected and was left untouched: '$partial'"
        }
        if ($partialLength -eq $entry.size) {
            if (-not (Test-ManifestHash -Path $partial -Entry $entry)) {
                throw "Completed partial file has a checksum mismatch and was left untouched: '$partial'"
            }
        }
    }

    if (-not (Test-Path -LiteralPath $partial -PathType Leaf) -or
        (Get-Item -LiteralPath $partial).Length -ne $entry.size) {
        $url = "https://huggingface.co/$($manifest.repo_id)/resolve/$($manifest.revision)/$($entry.name)?download=true"
        Write-Host "Downloading $($entry.name) from pinned revision $($manifest.revision)"
        & $curl.Source --fail --location --retry 4 --retry-delay 2 --connect-timeout 20 --continue-at "-" --output $partial $url
        if ($LASTEXITCODE -ne 0) {
            throw "curl.exe failed for '$($entry.name)' (exit $LASTEXITCODE). Partial data is preserved at '$partial'."
        }
    }

    if (-not (Test-ManifestHash -Path $partial -Entry $entry)) {
        throw "SHA-256 mismatch for '$partial'. File was preserved; expected $($entry.sha256)."
    }

    [System.IO.File]::Move($partial, $destination)
    Write-Host "Downloaded and verified $($entry.name)"
}

Write-Host "Chinese BERT is ready at '$target' (revision $($manifest.revision))."
Write-Host "This script does not download or redistribute G_7600.pth."
