param(
    [string]$ModelPath = "models\embedding\Qwen3-Embedding-0.6B-Q8_0.gguf",
    [string]$ModelName = "amadeus-qwen3-embedding",
    [int]$NumCtx = 8192
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$resolvedModelPath = Resolve-Path (Join-Path $root $ModelPath)
if (-not (Test-Path -LiteralPath $resolvedModelPath)) {
    throw "Embedding model not found: $resolvedModelPath"
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    throw "Ollama command not found. Install Ollama or set AMADEUS_MEMORY_EMBEDDING_BACKEND=openai/local_gguf."
}

$tmp = New-TemporaryFile
$modelfile = [System.IO.Path]::ChangeExtension($tmp.FullName, ".Modelfile")
Remove-Item -LiteralPath $tmp.FullName -Force

$fromPath = ($resolvedModelPath.Path -replace "\\", "/")
@"
FROM $fromPath
PARAMETER num_ctx $NumCtx
"@ | Set-Content -LiteralPath $modelfile -Encoding UTF8

try {
    Write-Host "Importing $ModelName from $resolvedModelPath"
    & ollama create $ModelName -f $modelfile
    if ($LASTEXITCODE -ne 0) {
        throw "ollama create failed with exit code $LASTEXITCODE"
    }
    Write-Host "Imported $ModelName"
} finally {
    if (Test-Path -LiteralPath $modelfile) {
        Remove-Item -LiteralPath $modelfile -Force
    }
}
