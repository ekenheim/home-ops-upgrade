<#
.SYNOPSIS
  Re-authorise the in-cluster TradingView MCP server (tradingview-mcp).

.DESCRIPTION
  Run from PowerShell on the Windows machine where the TradingView desktop
  app is installed and signed in. The only manual step is clicking Accept.

  Why it is done this way (see README.md):
    - TradingView only accepts a localhost OAuth callback, and mcp-remote in
      the pod listens on 127.0.0.1:3334. So this machine's localhost:3334 is
      port-forwarded to the pod.
    - Browsers get a CloudFront 403 on the authorize page; the TradingView
      desktop app does not. Start-Process hands the URL to the app intact
      (opening it from cmd/Run cuts it at the first '&').

.EXAMPLE
  .\reauth.ps1
  .\reauth.ps1 -Kubeconfig C:\path\to\kubeconfig
#>
param(
  [string]$Kubeconfig = "$HOME\kubeconfig",
  [string]$Kubectl = "$HOME\kubectl.exe",
  [string]$KubectlVersion = "v1.35.9"
)
$ErrorActionPreference = 'Stop'
$pod = 'tradingview-mcp-0'
$k = @('--kubeconfig', $Kubeconfig, '-n', 'llm')

if (-not (Test-Path $Kubeconfig)) {
  throw "No kubeconfig at $Kubeconfig. Copy it out of the dev container first:`n" +
        "  docker ps   # find the dev container (image name ends in -features)`n" +
        "  docker cp <container>:/workspaces/home-ops-upgrade/kubeconfig $Kubeconfig"
}
if (-not (Test-Path $Kubectl)) {
  Write-Host "Downloading kubectl $KubectlVersion to $Kubectl"
  curl.exe -fsSLo $Kubectl "https://dl.k8s.io/release/$KubectlVersion/bin/windows/amd64/kubectl.exe"
}
$busy = Get-NetTCPConnection -LocalPort 3334 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
  $owner = (Get-Process -Id $busy[0].OwningProcess).ProcessName
  throw "localhost:3334 is already in use by '$owner'. Close it (an old port-forward?) and re-run."
}

# 1. Restart the pod: a fresh 15-minute sign-in window, and a URL that is
#    guaranteed to belong to the running process.
Write-Host "Restarting $pod for a fresh sign-in..."
& $Kubectl @k delete pod $pod --wait=true | Out-Null
& $Kubectl @k wait --for=condition=Ready "pod/$pod" --timeout=180s | Out-Null

$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
  Start-Sleep -Seconds 3
  $m = & $Kubectl @k logs $pod -c mcp 2>$null |
    Select-String 'https://www\.tradingview\.com/mcp/oauth/authorize\S+'
  if ($m) { $url = $m[-1].Matches[0].Value }
}
if (-not $url) {
  # mcp-remote only prints a URL when its stored tokens no longer work.
  Write-Host "No sign-in requested: the stored token still works, nothing to do." -ForegroundColor Green
  Write-Host "If tools are failing anyway, check: $Kubectl $($k -join ' ') logs $pod -c mcp"
  return
}
# Margin for clock skew between this machine and the pod.
$started = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 120

# 2. Port-forward this machine's localhost:3334 to the pod's callback listener.
$pf = Start-Process -FilePath $Kubectl -PassThru -WindowStyle Hidden `
  -ArgumentList ($k + @('port-forward', "pod/$pod", '3334:3334'))
try {
  Start-Sleep -Seconds 3
  if ($pf.HasExited) { throw "Port-forward exited immediately; is localhost:3334 free?" }

  # 3. Hand the URL to the TradingView app.
  Write-Host "Opening the TradingView sign-in. Click Accept in the TradingView app."
  Start-Process $url

  # 4. Wait for mcp-remote to write a fresh token file to the PVC.
  $deadline = (Get-Date).AddMinutes(15)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $mtime = & $Kubectl @k exec $pod -c mcp -- sh -c `
      'stat -c %Y /data/.mcp-auth/mcp-remote-v1/*_tokens.json 2>/dev/null | sort -n | tail -1' 2>$null
    if ($mtime -and [int64]$mtime -ge $started) {
      Write-Host "Signed in. Token saved on the PVC." -ForegroundColor Green
      return
    }
  }
  throw "No new token after 15 minutes. See README.md, Troubleshooting."
}
finally {
  if (-not $pf.HasExited) { Stop-Process -Id $pf.Id }
}
