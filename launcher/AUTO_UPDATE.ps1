param([string]$FeedUrl="https://raw.githubusercontent.com/ska478111-netizen/NaverMailManager-Release/main/latest.json")
$ErrorActionPreference="Stop"
$root=Join-Path $env:LOCALAPPDATA "NaverMailManager"
$versions=Join-Path $root "versions"; $backup=Join-Path $root "backup"; $logs=Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $versions,$backup,$logs | Out-Null
$log=Join-Path $logs "updater.log"
function Log($m){ Add-Content -Encoding UTF8 $log ("{0} {1}" -f (Get-Date -Format s),$m) }
try {
  Log "update check"
  $m=Invoke-RestMethod -Uri $FeedUrl -UseBasicParsing
  if(-not $m.active){ Log "feed staged/inactive"; exit 0 }
  $curFile=Join-Path $root "current.txt"
  $cur=if(Test-Path $curFile){(Get-Content $curFile -Raw).Trim()}else{""}
  if($cur -eq $m.version){ Log "already current $cur"; exit 0 }
  $tmp=Join-Path $env:TEMP ("NMM-"+[guid]::NewGuid().ToString())
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  $zip=Join-Path $tmp "package.zip"
  Invoke-WebRequest -Uri $m.package_url -OutFile $zip -UseBasicParsing
  $hash=(Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  if($hash -ne $m.sha256.ToLower()){ throw "SHA256 mismatch" }
  $dest=Join-Path $versions $m.version
  $stage=Join-Path $tmp "stage"; Expand-Archive -Path $zip -DestinationPath $stage -Force
  $app=Get-ChildItem $stage -Recurse -Filter $m.entry | Select-Object -First 1
  if(-not $app){ throw "entry point missing" }
  $py=(Get-Command py -ErrorAction SilentlyContinue)
  if($py){ & py -3 -m py_compile $app.FullName } else { & python -m py_compile $app.FullName }
  if($LASTEXITCODE -ne 0){ throw "Python syntax validation failed" }
  if(Test-Path $dest){ Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Copy-Item (Join-Path $app.Directory.FullName "*") $dest -Recurse -Force
  if(Test-Path $curFile){ Copy-Item $curFile (Join-Path $backup ("current-"+(Get-Date -Format yyyyMMddHHmmss)+".txt")) -Force }
  Set-Content -Encoding ASCII $curFile $m.version
  Log "activated $($m.version)"
  Remove-Item $tmp -Recurse -Force
  exit 0
} catch {
  Log ("FAILED: "+$_.Exception.Message)
  exit 1
}
