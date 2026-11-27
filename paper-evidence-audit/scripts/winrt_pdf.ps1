# Windows-native PDF helper: renders pages through Windows.Data.Pdf and OCRs page
# images through Windows.Media.Ocr. Both APIs ship with Windows 10/11, so this file
# gives the Skill zero-install rasterisation and OCR where no Python wheel or poppler
# exists. Requires Windows PowerShell 5.1 (the only host that projects WinRT types).
# ASCII-only on purpose so Windows PowerShell parses it under any active code page.
param(
    [string]$Mode = "render",
    [string]$Pdf,
    [string]$OutDir,
    [string]$Pages = "",
    [int]$Dpi = 200,
    [int]$MaxWidth = 4000,
    [string]$Image,
    [string]$Lang = "",
    [switch]$Probe
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskOp = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]

function Await-Op($op, $type) {
    $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
function Await-Action($action) {
    $t = $asTaskAction.Invoke($null, @($action))
    $t.Wait(-1) | Out-Null
}

if ($Mode -eq "ocr") {
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]

    $langs = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    Write-Output ("AVAILABLE_LANGS=" + ($langs -join ','))
    if ($Probe) { exit 0 }
    if ($langs.Count -eq 0) { Write-Output "OCR_ENGINE=None"; exit 2 }

    $engine = $null
    if ($Lang -ne "") {
        $requested = New-Object Windows.Globalization.Language($Lang)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($requested)
    }
    if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if ($null -eq $engine) { Write-Output "OCR_ENGINE=None"; exit 2 }
    Write-Output ("ENGINE_LANG=" + $engine.RecognizerLanguage.LanguageTag)

    $file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path -LiteralPath $Image).Path)) ([Windows.Storage.StorageFile])
    $stream = Await-Op ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await-Op ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($ln in $result.Lines) { $lines += $ln.Text }
    Write-Output ("LINES=" + $lines.Count)
    Write-Output "-----BEGIN_OCR_TEXT-----"
    $lines | ForEach-Object { Write-Output $_ }
    Write-Output "-----END_OCR_TEXT-----"
    exit 0
}

# ---- render / count mode ----
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType = WindowsRuntime]

$file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path -LiteralPath $Pdf).Path)) ([Windows.Storage.StorageFile])
$doc = Await-Op ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$count = [int]$doc.PageCount
Write-Output ("PAGE_COUNT={0}" -f $count)
if ($Mode -eq "count") { Write-Output "BACKEND=winrt_pdf"; exit 0 }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$want = @()
if ([string]::IsNullOrWhiteSpace($Pages)) {
    if ($count -gt 0) { $want = 1..$count }
}
else {
    foreach ($part in ($Pages -split ',')) {
        $part = $part.Trim()
        if ($part -match '^(\d+)-(\d+)$') { $want += ([int]$Matches[1])..([int]$Matches[2]) }
        elseif ($part -match '^\d+$') { $want += [int]$part }
    }
}
$want = $want | Where-Object { $_ -ge 1 -and $_ -le $count } | Sort-Object -Unique

$made = 0
foreach ($p in $want) {
    $page = $doc.GetPage([uint32]($p - 1))
    $target = [int]([math]::Round(([double]$page.Size.Width / 72.0) * $Dpi))
    if ($target -lt 1) { $target = 1 }
    if ($target -gt $MaxWidth) { $target = $MaxWidth }

    $opts = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $opts.DestinationWidth = [uint32]$target
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $act = $page.RenderToStreamAsync($stream, $opts)
    Await-Action $act

    $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
    $name = Join-Path $OutDir ("page_{0:D4}.png" -f $p)
    $fs = [System.IO.File]::Create($name)
    $netStream.CopyTo($fs)
    $fs.Close(); $netStream.Close(); $stream.Dispose(); $page.Dispose()

    Write-Output ("RENDERED {0} {1} {2}" -f $p, $name, (Get-Item -LiteralPath $name).Length)
    $made++
}
Write-Output "BACKEND=winrt_pdf"
Write-Output ("OK={0}" -f $made)
exit 0
