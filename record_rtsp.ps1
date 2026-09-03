param(
    [string]$Url = "rtsp://192.168.66.1:8554/live",
    [string]$OutputDirectory = ".\recordings"
)

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Error "ffmpeg was not found. Install ffmpeg or use VLC/OBS to record the RTSP stream."
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outputFile = Join-Path $OutputDirectory "ball_test_$timestamp.mkv"

Write-Host "Recording $Url"
Write-Host "Output: $outputFile"
Write-Host "Press Ctrl+C after the test."

& $ffmpeg.Source `
    -hide_banner `
    -loglevel warning `
    -rtsp_transport tcp `
    -i $Url `
    -map 0:v:0 `
    -c:v copy `
    -f matroska `
    $outputFile
