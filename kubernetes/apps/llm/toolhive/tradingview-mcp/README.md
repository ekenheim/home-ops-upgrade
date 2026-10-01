# tradingview-mcp

TradingView's hosted MCP server (<https://www.tradingview.com/mcp/docs>), signed in once
with Robin's TradingView account and shared through `mcp-gateway` as `tradingview-mcp_*`
tools. Read-only: the ten watchlist/alert write tools are dropped by `mcp-remote`
(`--ignore-tool`) and are not in the `MCPToolConfig` allow list.

## How it works

```
clients ─► mcp-gateway (vMCP) ─► tradingview-mcp proxy ─► mcp-remote (stdio) ─OAuth─► mcp.tradingview.com/mcp
                                                           tokens on PVC /data/.mcp-auth
```

TradingView only offers per-user OAuth 2.1 (browser sign-in, Essential plan or above).
`mcp-remote` registers an OAuth client, keeps the refresh token on the `tradingview-mcp` PVC,
and refreshes it itself. A sign-in is only needed again when the refresh token stops working.

## Signs it needs a new sign-in

- `tradingview-mcp_*` tools are missing from the gateway, or calls to them fail.
- The pod log asks for authorization:

  ```sh
  kubectl -n llm logs tradingview-mcp-0 -c mcp | grep -A1 'Please authorize'
  ```

## Re-auth: run the script

On the **Windows machine with the TradingView desktop app** (signed in), in PowerShell:

```powershell
cd <repo>\kubernetes\apps\llm\toolhive\tradingview-mcp   # or copy reauth.ps1 anywhere
.\reauth.ps1
```

Then click **Accept** in the TradingView app. The script prints `Signed in` when the new
token is on the PVC. Each step is explained in the script.

Prerequisites (the script checks for both):

- `$HOME\kubeconfig`: copy it out of the dev container if it isn't there:

  ```powershell
  docker ps                       # the dev container's image name ends in -features
  docker cp <container>:/workspaces/home-ops-upgrade/kubeconfig $HOME\kubeconfig
  ```

  It is cluster-admin. Keep it in your profile folder, not somewhere shared.
- `$HOME\kubectl.exe`: the script downloads it if missing. `winget` is not on this machine.

If PowerShell refuses to run the script (execution policy), run it once with:
`powershell -ExecutionPolicy Bypass -File .\reauth.ps1`

## Re-auth by hand (what the script does)

1. Restart the pod for a fresh 15-minute window (`--auth-timeout 900`):
   `kubectl -n llm delete pod tradingview-mcp-0`
2. **Window 1**, leave running: `kubectl -n llm port-forward pod/tradingview-mcp-0 3334:3334`.
   Check that `http://localhost:3334/` in a browser shows `Cannot GET /`.
3. **Window 2**: fetch the URL and hand it to the TradingView app intact, one line at a time:

   ```powershell
   $log = .\kubectl.exe --kubeconfig .\kubeconfig -n llm logs tradingview-mcp-0 -c mcp
   $m = $log | Select-String 'https://www\.tradingview\.com/mcp/oauth/authorize\S+'
   $u = $m[-1].Matches[0].Value
   $u.Length          # ~378; much shorter means it was cut
   Start-Process $u
   ```

4. Click **Accept** in the TradingView app. The pod logs a `tools/list` and writes
   `/data/.mcp-auth/mcp-remote-v1/*_tokens.json`.

## Troubleshooting

What went wrong on the first sign-in (2026-10-01), and why:

| Symptom | Cause | Fix |
|---|---|---|
| CloudFront `403 ERROR … Request blocked` | TradingView's WAF blocks the authorize page in browsers (Chrome, incognito, phone) | Open the URL in the **desktop app** with `Start-Process $u` |
| Clicking the link opens the TradingView app | The app is the registered handler for tradingview.com links | Expected. The app is the route that works |
| `Missing "redirect_uri" parameter` | URL cut at an `&` (opened from cmd/Run, or a wrapped paste) | Use `Start-Process $u` from PowerShell |
| `PKCE is required: missing "code_challenge"` on a `localhost:3334` page | URL truncated before `code_challenge`. The page itself is the pod, so the forward works | Re-fetch `$u` and check `$u.Length` |
| `localhost:3334` refused (`ERR_CONNECTION_REFUSED`) | Nothing forwards this machine's port 3334 to the pod | Run the port-forward **on Windows**, not in the dev container |
| `Unable to listen on port 3334 … address already in use` | Another port-forward already holds 3334 | Close it, or use it as it is |
| VS Code "Unable to forward localhost:3334" | That VS Code window isn't attached to the dev container | Don't use VS Code for this. Forward from Windows |
| No URL in the log | The stored token still works, so nothing to do | |

## Why not ToolHive's embedded auth server

ToolHive can do the upstream OAuth itself (`MCPExternalAuthConfig` `embeddedAuthServer` +
`upstreamInject`). But it keeps upstream tokens **per authenticated client session**, so the
gateway would stop being anonymous and every device or app would sign in separately.
TradingView also rejects non-localhost `http` redirect URIs (`Redirect URI must use https or
be an RFC 8252 loopback http URI`), and `mcp-remote` can only build `http://host:port/...`
callbacks. That rules out a cluster-hosted callback address, so the sign-in goes through a
port-forward.
