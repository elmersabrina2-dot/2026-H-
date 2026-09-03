param(
    [string]$Url = "rtsp://192.168.66.1:8554/live"
)

$vlcCandidates = @(
    (Join-Path $env:ProgramFiles "VideoLAN\VLC\vlc.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "VideoLAN\VLC\vlc.exe")
)

$vlcPath = $vlcCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $vlcPath) {
    $vlcCommand = Get-Command "vlc.exe" -ErrorAction SilentlyContinue
    if ($vlcCommand) {
        $vlcPath = $vlcCommand.Source
    }
}

if ($vlcPath) {
    Start-Process -FilePath $vlcPath -ArgumentList @(
        "--network-caching=150",
        $Url
    )
    exit 0
}

$ffplayCommand = Get-Command "ffplay.exe" -ErrorAction SilentlyContinue
if ($ffplayCommand) {
    Start-Process -FilePath $ffplayCommand.Source -ArgumentList @(
        "-rtsp_transport", "tcp",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        $Url
    )
    exit 0
}

Write-Host "No VLC or ffplay was found." -ForegroundColor Yellow
Write-Host "Install VLC, connect the computer to Wi-Fi MaixCAM-Ball,"
Write-Host "then run this script again."
Write-Host "RTSP URL: $Url"
Read-Host "Press Enter to close"
