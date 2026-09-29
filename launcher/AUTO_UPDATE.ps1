param([string]$FeedUrl="https://raw.githubusercontent.com/ska478111-netizen/NaverMailManager-Release/main/latest.json")
$ErrorActionPreference="Stop"
$root=Join-Path $env:LOCALAPPDATA "NaverMailManager"
$versions=Join-Path $root "versions"; $backup=Join-Path $root "backup"; $logs=Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $versions,$backup,$logs | Out-Null
$log=Join-Path $logs "updater.log"
function Log($m){Add-Content -Encoding UTF8 $log ("{0} {1}" -f (Get-Date -Format s),$m)}
function PythonCheck($p){$py=Get-Command py -ErrorAction SilentlyContinue;if($py){& py -3 -m py_compile $p}else{& python -m py_compile $p};if($LASTEXITCODE -ne 0){throw "Python syntax validation failed"}}
try{
$m=Invoke-RestMethod -Uri $FeedUrl -UseBasicParsing
if(-not $m.active){Log "feed inactive";exit 0}
if($m.deployment_mode -ne "text-files"){throw "Unsupported deployment mode"}
$curFile=Join-Path $root "current.txt";$cur=if(Test-Path $curFile){(Get-Content $curFile -Raw).Trim()}else{""}
if($cur -eq $m.version){Log "already current $cur";exit 0}
$tmp=Join-Path $env:TEMP ("NMM-"+[guid]::NewGuid());$stage=Join-Path $tmp "stage";New-Item -ItemType Directory -Force -Path $stage|Out-Null
foreach($f in $m.files){$out=Join-Path $stage $f.path;New-Item -ItemType Directory -Force -Path (Split-Path $out)|Out-Null;Invoke-WebRequest -Uri $f.url -OutFile $out -UseBasicParsing;if((Get-Item $out).Length -ne [int64]$f.size){throw "Size mismatch: $($f.path)"};$h=(Get-FileHash $out -Algorithm SHA256).Hash.ToLower();if($h -ne $f.sha256.ToLower()){throw "SHA256 mismatch: $($f.path)"}}
$entry=Join-Path $stage $m.entry;if(-not(Test-Path $entry)){throw "Entry point missing"};PythonCheck $entry
$dest=Join-Path $versions $m.version;if(Test-Path $dest){Remove-Item $dest -Recurse -Force};New-Item -ItemType Directory -Force -Path $dest|Out-Null;Copy-Item (Join-Path $stage "*") $dest -Recurse -Force
if(Test-Path $curFile){Copy-Item $curFile (Join-Path $backup ("current-"+(Get-Date -Format yyyyMMddHHmmss)+".txt")) -Force}
Set-Content -Encoding ASCII $curFile $m.version;Log "activated $($m.version)";Remove-Item $tmp -Recurse -Force;exit 0
}catch{Log ("FAILED: "+$_.Exception.Message);exit 1}
