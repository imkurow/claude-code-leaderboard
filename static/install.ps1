# __LB_TITLE__ - Claude Code telemetry setup (Windows PowerShell)
#
#   ⟦Install‖Pasang⟧ : ⟦get the full command (with -Headers CF-Access-*) from the admin‖perintah lengkap (dengan -Headers CF-Access-*) didapat dari admin⟧
#   ⟦Remove‖Hapus⟧  : $env:LB_UNINSTALL='1'; ⟦then the same‖lalu perintah⟧ irm ... | iex ⟦command‖yang sama⟧
#   9router : $env:LB_9ROUTER='1' ⟦before the command, on the Windows PC that runs 9router (it sets up a sync instead of Claude Code telemetry)‖sebelum perintahnya, di PC Windows yang menjalankan 9router (memasang sinkron, bukan telemetry Claude Code)⟧
#   JRouter : $env:LB_JROUTER='1' ⟦the same for JRouter‖sama, untuk JRouter⟧
#
# ⟦What changes: the "env" block in %USERPROFILE%\.claude\settings.json (a backup is made first).‖Yang diubah: blok "env" di %USERPROFILE%\.claude\settings.json (backup dibuat dulu).⟧
# ⟦What Claude Code sends: metric numbers only. Prompt contents, code, and files are NOT sent.‖Yang dikirim Claude Code: angka metrik saja. Isi prompt, kode, dan file TIDAK ikut terkirim.⟧
& {
  $ErrorActionPreference = 'Stop'
  $Endpoint = '__LB_URL__/v1/metrics'
  $CfId = '__LB_CF_ID__'; $CfSecret = '__LB_CF_SECRET__'
  if ($CfId.StartsWith('__')) { $CfId = '' }
  if ($CfSecret.StartsWith('__')) { $CfSecret = '' }
  $Uninstall = ($env:LB_UNINSTALL -eq '1')
  $Router = if ($env:LB_JROUTER -eq '1') { 'jrouter' } elseif ($env:LB_9ROUTER -eq '1') { '9router' } else { '' }
  $Token = "$env:LB_TOKEN"
  $Dir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
  $Path = Join-Path $Dir 'settings.json'
  $Keys = @('OTEL_EXPORTER_OTLP_METRICS_PROTOCOL', 'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT',
            'OTEL_EXPORTER_OTLP_METRICS_HEADERS', 'OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE',
            'OTEL_METRIC_EXPORT_INTERVAL')

  if (-not $Uninstall) {
    if ($Token -notmatch '^[A-Za-z0-9_-]+$') { Write-Host "⟦Set the token first‖Set token dulu⟧: `$env:LB_TOKEN='<TOKEN>'" -ForegroundColor Red; return }
    $Hdr = @{ 'x-lb-token' = $Token }
    $OtelHeaders = "x-lb-token=$Token"
    if ($CfId) {
      $Hdr['CF-Access-Client-Id'] = $CfId; $Hdr['CF-Access-Client-Secret'] = $CfSecret
      $OtelHeaders += ",CF-Access-Client-Id=$CfId,CF-Access-Client-Secret=$CfSecret"
    }
    Write-Host -NoNewline '⟦Checking token with the server... ‖Cek token ke server... ⟧'
    try {
      Invoke-RestMethod -Method Post -Uri $Endpoint -ContentType 'application/json' -MaximumRedirection 0 `
        -Headers $Hdr -Body '{"resourceMetrics":[]}' | Out-Null
      Write-Host 'OK'
    } catch {
      Write-Host "⟦failed: the token is wrong/revoked or the server is unreachable.‖gagal: token salah/dicabut atau server tidak bisa dihubungi.⟧ ($($_.Exception.Message))" -ForegroundColor Red
      return
    }
  }

  # ---------------------------------------------------------------------------
  # 9router or JRouter (a 9router fork with the same usage log): read the router's own usage log
  # every minute (Task Scheduler) and send hourly totals per model. The sync is a Node.js
  # script; both routers run on Node, so Node is there.
  # ---------------------------------------------------------------------------
  if ($Router) {
    $RName = if ($Router -eq 'jrouter') { 'JRouter' } else { '9router' }
    $RDir = Join-Path $env:LOCALAPPDATA 'claude-leaderboard'
    $RTask = "ClaudeLeaderboard$Router"
    $RScript = Join-Path $RDir 'router-sync.js'
    $RVbs = Join-Path $RDir "run-$Router.vbs"
    $RFiles = @("$Router.json", "$Router.state", "$Router.lock", "$Router-sync.log", "run-$Router.vbs", '9router-sync.js', 'run-hidden.vbs') | ForEach-Object { Join-Path $RDir $_ }
    if ($Uninstall) {
      Unregister-ScheduledTask -TaskName $RTask -Confirm:$false -ErrorAction SilentlyContinue
      Remove-Item -Force -ErrorAction SilentlyContinue $RFiles
      if (-not (Get-ChildItem -Path $RDir -Filter '*.json' -ErrorAction SilentlyContinue)) { Remove-Item -Force -ErrorAction SilentlyContinue $RScript }
      Write-Host "$RName ⟦sync removed. Data already on the leaderboard stays.‖sinkron dihapus. Data yang sudah ada di leaderboard tetap.⟧"
      Remove-Item Env:LB_TOKEN, Env:LB_UNINSTALL, Env:LB_9ROUTER, Env:LB_JROUTER -ErrorAction SilentlyContinue
      return
    }
    $Node = $env:LB_NODE
    if (-not $Node) { $cmd = Get-Command node.exe -ErrorAction SilentlyContinue; if ($cmd) { $Node = $cmd.Source } }
    if (-not $Node) {
      $proc = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match $Router } | Select-Object -First 1
      if ($proc) { $Node = $proc.ExecutablePath }
    }
    if (-not $Node -or -not (Test-Path $Node)) {
      Write-Host "⟦Node.js was not found. Point to the node.exe that runs‖Node.js tidak ditemukan. Tunjukkan dulu node.exe yang menjalankan⟧ ${RName}: `$env:LB_NODE='C:\path\node.exe'" -ForegroundColor Red; return
    }
    $RData = if ($env:LB_ROUTER_DIR) { $env:LB_ROUTER_DIR } elseif ($env:LB_9R_DIR) { $env:LB_9R_DIR } elseif ($env:DATA_DIR -and $env:DATA_DIR -notmatch '^/') { $env:DATA_DIR } else { Join-Path $env:APPDATA $Router }
    $RDb = if ($env:LB_ROUTER_DB) { $env:LB_ROUTER_DB } elseif ($env:LB_9R_DB) { $env:LB_9R_DB } else { Join-Path $RData 'db\data.sqlite' }
    if (-not (Test-Path $RDb)) {
      Write-Host "$RName ⟦database was not found at‖database tidak ditemukan di⟧ $RDb. ⟦Run this as the Windows user that runs it, or set‖Jalankan sebagai user Windows yang menjalankannya, atau set⟧ `$env:LB_ROUTER_DB='C:\path\data.sqlite'" -ForegroundColor Red; return
    }
    New-Item -ItemType Directory -Force -Path $RDir | Out-Null
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$Endpoint/router-sync.js" -Headers $Hdr -OutFile $RScript -MaximumRedirection 0
    } catch {
      Write-Host "⟦Could not download the router sync from the server.‖Gagal mengunduh sinkron router dari server.⟧ ($($_.Exception.Message))" -ForegroundColor Red; return
    }
    $keys = if ($env:LB_ROUTER_KEYS) { $env:LB_ROUTER_KEYS } else { "$env:LB_9R_KEYS" }
    $cfg = [ordered]@{ url = '__LB_URL__'; token = $Token; cfId = $CfId; cfSecret = $CfSecret; db = $RDb; dataDir = $RData; keys = $keys }
    [IO.File]::WriteAllText((Join-Path $RDir "$Router.json"), ($cfg | ConvertTo-Json))
    if ($Router -eq '9router') {
      # an earlier 9router sync: same task name and machine id, so it is replaced and nothing counts twice
      Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $RDir '9router-sync.js'), (Join-Path $RDir 'run-hidden.vbs')
    }
    Write-Host -NoNewline "⟦Sending‖Kirim pemakaian⟧ $RName ⟦usage (up to 90 days)... ‖(maks. 90 hari)... ⟧"
    & $Node --no-warnings $RScript "--kind=$Router" --force
    if ($LASTEXITCODE -ne 0) { Write-Host '⟦failed, see the message above.‖gagal, lihat pesan di atas.⟧' -ForegroundColor Red; return }
    # wscript runs node without flashing a console window every minute
    $q = '"'
    [IO.File]::WriteAllText($RVbs, 'CreateObject("WScript.Shell").Run ' + $q + $q + $q + $Node + $q + $q + ' --no-warnings ' + $q + $q + $RScript + $q + $q + ' --kind=' + $Router + $q + ', 0, False' + "`r`n")
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ($q + $RVbs + $q)
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $RTask -Action $action -Trigger $trigger -Settings $settings -Force `
      -Description "__LB_TITLE__: send $RName usage every minute" | Out-Null
    Write-Host "⟦Done.‖Selesai.⟧ $RName ⟦usage now reaches‖sekarang masuk ke⟧ __LB_URL__ ⟦every minute (Task Scheduler: $RTask).‖tiap menit (Task Scheduler: $RTask).⟧"
    Remove-Item Env:LB_TOKEN, Env:LB_9ROUTER, Env:LB_JROUTER -ErrorAction SilentlyContinue
    return
  }

  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  $obj = [pscustomobject]@{}
  if (Test-Path $Path) {
    $raw = [IO.File]::ReadAllText($Path)
    if ($raw.Trim()) { $obj = $raw | ConvertFrom-Json }
    $backup = "$Path.bak.$(Get-Date -Format yyyyMMddHHmmss)"
    Copy-Item $Path $backup
    Write-Host "Backup: $backup"
  }
  if (-not ($obj.PSObject.Properties.Name -contains 'env') -or $null -eq $obj.env) {
    $obj | Add-Member -Force -NotePropertyName env -NotePropertyValue ([pscustomobject]@{})
  }
  $envObj = $obj.env
  function Set-Key($k, $v) { $envObj | Add-Member -Force -NotePropertyName $k -NotePropertyValue $v }
  function Get-Key($k) { if ($envObj.PSObject.Properties.Name -contains $k) { "$($envObj.$k)" } else { '' } }

  $exporters = @((Get-Key 'OTEL_METRICS_EXPORTER').Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne 'none' })
  if (-not $Uninstall) {
    Set-Key 'CLAUDE_CODE_ENABLE_TELEMETRY' '1'
    if ($exporters -notcontains 'otlp') { $exporters += 'otlp' }
    Set-Key 'OTEL_METRICS_EXPORTER' ($exporters -join ',')
    Set-Key 'OTEL_EXPORTER_OTLP_METRICS_PROTOCOL' 'http/json'
    Set-Key 'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT' $Endpoint
    Set-Key 'OTEL_EXPORTER_OTLP_METRICS_HEADERS' $OtelHeaders
    Set-Key 'OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE' 'delta'
    Set-Key 'OTEL_METRIC_EXPORT_INTERVAL' '5000'
  } else {
    foreach ($k in $Keys) { $envObj.PSObject.Properties.Remove($k) }
    if (-not (Get-Key 'OTEL_EXPORTER_OTLP_ENDPOINT')) {
      $rest = @($exporters | Where-Object { $_ -ne 'otlp' })
      if ($rest.Count) { Set-Key 'OTEL_METRICS_EXPORTER' ($rest -join ',') } else { $envObj.PSObject.Properties.Remove('OTEL_METRICS_EXPORTER') }
    }
    if (-not @($envObj.PSObject.Properties.Name | Where-Object { $_ -like 'OTEL_*' }).Count) {
      $envObj.PSObject.Properties.Remove('CLAUDE_CODE_ENABLE_TELEMETRY')
    }
    if (-not @($envObj.PSObject.Properties).Count) { $obj.PSObject.Properties.Remove('env') }
  }

  $json = $obj | ConvertTo-Json -Depth 64
  [IO.File]::WriteAllText($Path, $json + "`n", (New-Object Text.UTF8Encoding $false))
  if ($Uninstall) {
    Write-Host "⟦Leaderboard telemetry removed from‖Telemetry leaderboard dihapus dari⟧ $Path."
    Remove-Item Env:LB_TOKEN, Env:LB_UNINSTALL -ErrorAction SilentlyContinue
    return
  }

  # Backfill: history from before telemetry was enabled, read from Claude Code's local transcripts.
  # Only hourly token totals per model and hourly session counts are sent. Safe to repeat.
  Write-Host -NoNewline '⟦Importing usage history from local transcripts... ‖Ambil riwayat pemakaian dari transkrip lokal... ⟧'
  try {
    $win = Invoke-RestMethod -Method Get -Uri "$Endpoint/backfill" -Headers $Hdr -MaximumRedirection 0
    $start = [int64]$win.start; $cutoff = [int64]$win.cutoff
    $sha = [Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("$env:COMPUTERNAME|$env:USERNAME"))
    $machine = (-join ($hash | ForEach-Object { $_.ToString('x2') })).Substring(0, 16)
    $roots = @((Join-Path $Dir 'projects'), (Join-Path $HOME '.config\claude\projects')) | Select-Object -Unique
    $startUtc = [DateTimeOffset]::FromUnixTimeSeconds($start).UtcDateTime
    $seen = @{}; $agg = @{}; $sess = @{}
    $files = 0; $msgs = 0; $output = [int64]0
    foreach ($root in $roots) {
      if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
      foreach ($f in Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.jsonl' -ErrorAction SilentlyContinue) {
        if ($f.LastWriteTimeUtc -lt $startUtc) { continue }
        $files++
        foreach ($line in [IO.File]::ReadLines($f.FullName)) {
          if (-not $line.Contains('"usage"') -or -not $line.Contains('"assistant"')) { continue }
          try { $o = $line | ConvertFrom-Json } catch { continue }
          if ($o.type -ne 'assistant' -or -not $o.message -or -not $o.message.usage) { continue }
          $m = $o.message; $u = $m.usage; $model = "$($m.model)"
          if (-not $model -or $model -eq '<synthetic>') { continue }
          $t = $o.timestamp
          try {
            if ($t -is [datetime]) { $ts = ([DateTimeOffset]$t.ToUniversalTime()).ToUnixTimeSeconds() }
            else { $ts = [DateTimeOffset]::Parse("$t", [Globalization.CultureInfo]::InvariantCulture).ToUnixTimeSeconds() }
          } catch { continue }
          if ($ts -lt $start -or $ts -ge $cutoff) { continue }
          if ($m.id) {
            $id = "$($m.id):$($o.requestId)"
            if ($seen.ContainsKey($id)) { continue }
            $seen[$id] = 1
          }
          $hour = $ts - ($ts % 3600)
          $key = "$hour|$model"
          if (-not $agg.ContainsKey($key)) {
            $agg[$key] = [ordered]@{ hour = [int64]$hour; model = $model; input = [int64]0; output = [int64]0; cache_read = [int64]0; cache_creation = [int64]0 }
          }
          $a = $agg[$key]
          $a.input += [int64]$u.input_tokens
          $a.output += [int64]$u.output_tokens
          $a.cache_read += [int64]$u.cache_read_input_tokens
          $a.cache_creation += [int64]$u.cache_creation_input_tokens
          $output += [int64]$u.output_tokens
          $msgs++
          if ($o.sessionId -and -not $o.isSidechain) {
            $sid = "$($o.sessionId)"
            if (-not $sess.ContainsKey($sid) -or $ts -lt $sess[$sid]) { $sess[$sid] = $ts }
          }
        }
      }
    }
    if ($msgs -eq 0) {
      Write-Host '⟦no history from before telemetry was enabled.‖tidak ada riwayat sebelum telemetry aktif.⟧'
    } else {
      $perHour = @{}
      foreach ($v in $sess.Values) { $h = $v - ($v % 3600); $perHour[$h] = 1 + [int]$perHour[$h] }
      $payload = [ordered]@{
        machine = $machine; messages = $msgs; files = $files
        rows = @($agg.Values)
        sessions = @($perHour.Keys | ForEach-Object { [ordered]@{ hour = [int64]$_; count = $perHour[$_] } })
      }
      $body = [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress))
      Invoke-RestMethod -Method Post -Uri "$Endpoint/backfill" -Headers $Hdr -ContentType 'application/json' `
        -Body $body -MaximumRedirection 0 | Out-Null
      Write-Host "OK, $msgs ⟦responses from‖respons dari⟧ $files ⟦files‖file⟧ ($output ⟦output tokens‖output token⟧) ⟦added to the leaderboard.‖masuk leaderboard.⟧"
    }
  } catch {
    Write-Host "⟦skipped‖dilewati⟧ ($($_.Exception.Message))."
  }

  Write-Host '⟦Done. Restart Claude Code; new data shows up at‖Selesai. Tutup lalu buka lagi Claude Code; data baru muncul di⟧ __LB_URL__ ⟦within ~1 minute of use.‖dalam ~1 menit setelah dipakai.⟧'
  Remove-Item Env:LB_TOKEN, Env:LB_UNINSTALL -ErrorAction SilentlyContinue
}
