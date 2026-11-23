<# 检测并创建项目专用 Python 环境，启动本地服务。 / Prepare a verified local Python environment and start the application. #>
[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$NoBrowser,
    [switch]$Offline
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$nativeModules = Join-Path $PSHOME 'Modules'
if (($env:PSModulePath -split ';') -notcontains $nativeModules) {
    $env:PSModulePath = $nativeModules + ';' + $env:PSModulePath
}
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $projectRoot '.runtime'
$cacheRoot = Join-Path $runtimeRoot 'cache'
$wheelRoot = Join-Path $cacheRoot 'wheels'
$baseDir = Join-Path $runtimeRoot 'python-3.14.9'
$basePython = Join-Path $baseDir 'python.exe'
$envDir = Join-Path $projectRoot '.venv'
$envPython = Join-Path $envDir 'Scripts\python.exe'
$lockPath = Join-Path $projectRoot 'requirements.lock'
$zipName = 'python-3.14.9-amd64.zip'
# Python install manager's official pythoncore-3.14-64 package, not the embeddable distribution.
# Source: https://www.python.org/ftp/python/3.14.9/windows-3.14.9.json
$zipHash = '874d01bac04b470c6e1422dfccb9e182b9efe90c6a8adc5fc50d66bf84c40191'

function Test-Interpreter([string]$Path) {
    if (-not [IO.File]::Exists($Path)) { return $false }
    $ErrorActionPreference = 'Continue'
    try {
        $result = & $Path -I -c "import struct,sys,sysconfig; print('ok' if sys.version_info[:3]==(3,14,9) and struct.calcsize('P')==8 and not sysconfig.get_config_var('Py_GIL_DISABLED') else 'other')" 2>$null
        return ($LASTEXITCODE -eq 0 -and $result -eq 'ok')
    } catch { return $false }
}

function Test-Dependencies([switch]$ExplainFailure) {
    if (-not [IO.File]::Exists($envPython)) { return $false }
    $ErrorActionPreference = 'Continue'
    $probe = @'
import importlib.metadata as meta
import pathlib, sqlite3, struct, sys
from zoneinfo import ZoneInfo
import flask, openpyxl, waitress
lock, folder = map(pathlib.Path, sys.argv[1:])
assert sys.version_info[:3] == (3, 14, 9) and struct.calcsize("P") == 8
assert pathlib.Path(sys.prefix).resolve().samefile(folder)
assert pathlib.Path(sys.base_prefix).resolve().samefile(folder.parent / '.runtime' / 'python-3.14.9')
assert sqlite3.sqlite_version == "3.50.4" and meta.version("pip") == "26.2.2"
assert ZoneInfo("Europe/Berlin")
for line in lock.read_text(encoding="utf-8-sig").splitlines():
    if "==" in line and not line.startswith("#"):
        package, version = line.split()[0].split("==")
        assert meta.version(package) == version
print("ok")
'@
    try {
        $result = @($probe | & $envPython -I -X utf8 - $lockPath $envDir 2>&1)
        if ($LASTEXITCODE -eq 0 -and $result -contains 'ok') { return $true }
        if ($ExplainFailure) { foreach ($line in $result) { [Console]::Error.WriteLine($line.ToString()) } }
        return $false
    } catch { return $false }
}

function Get-Digest([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
}

function Assert-FileHash([string]$Path, [string]$Expected) {
    return ([IO.File]::Exists($Path) -and (Get-Digest $Path) -ieq $Expected)
}

function Save-VerifiedFile([string]$Uri, [string]$Path, [string]$Expected) {
    if (Assert-FileHash $Path $Expected) { return }
    if ($Offline) { throw "离线缓存缺失或校验失败。请补齐 $Path，再启动。" }
    $partial = $Path + '.download-' + [Guid]::NewGuid().ToString('N')
    try {
        Write-Host ('正在获取 ' + [IO.Path]::GetFileName($Path))
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $partial -TimeoutSec 180
        if (-not (Assert-FileHash $partial $Expected)) { throw "文件校验失败：$Uri" }
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($partial, $Path, [System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($partial, $Path) }
    } finally {
        if ([IO.File]::Exists($partial)) { [IO.File]::Delete($partial) }
    }
}

function Get-WheelCache {
    $available = @{}
    foreach ($wheel in [IO.Directory]::EnumerateFiles($wheelRoot, '*.whl')) {
        $digest = Get-Digest $wheel
        $available[$digest] = $true
    }
    foreach ($line in [IO.File]::ReadAllLines($lockPath)) {
        if ($line.Trim() -eq '' -or $line.StartsWith('#')) { continue }
        if ($line -notmatch '^([A-Za-z0-9._-]+)==([A-Za-z0-9.]+)\s+--hash=sha256:([a-f0-9]{64})$') {
            throw "requirements.lock 的格式无效。"
        }
        $package, $version, $hash = $Matches[1], $Matches[2], $Matches[3]
        if ($available.ContainsKey($hash)) { continue }
        if ($Offline) { throw "离线缓存缺少 $package $version。请补齐 .runtime\cache\wheels。" }
        $release = Invoke-RestMethod -Uri "https://pypi.org/pypi/$package/$version/json" -TimeoutSec 45
        $wheel = @($release.urls | Where-Object { $_.packagetype -eq 'bdist_wheel' -and $_.digests.sha256 -eq $hash })
        if ($wheel.Count -ne 1) { throw "PyPI 上找不到匹配锁定校验值的 $package $version。" }
        $name = $wheel[0].filename
        $downloadUri = [Uri]$wheel[0].url
        if ($name -ne [IO.Path]::GetFileName($name) -or $downloadUri.Scheme -ne 'https' -or $downloadUri.Host -ne 'files.pythonhosted.org') {
            throw "软件包下载地址异常。"
        }
        Save-VerifiedFile $downloadUri.AbsoluteUri (Join-Path $wheelRoot $name) $hash
    }
}

function Install-BasePython {
    if (Test-Interpreter $basePython) { return }
    $archive = Join-Path $cacheRoot $zipName
    Save-VerifiedFile "https://www.python.org/ftp/python/3.14.9/$zipName" $archive $zipHash
    $stage = Join-Path $runtimeRoot ('extract-' + [Guid]::NewGuid().ToString('N'))
    Write-Host '正在本项目内解压 Python 3.14.9 …'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
    if (-not (Test-Interpreter (Join-Path $stage 'python.exe'))) {
        throw "Python 运行时自检失败。解压目录为 $stage"
    }
    # Preserve an incomplete earlier installation for diagnosis; never replace user data.
    if ([IO.Directory]::Exists($baseDir)) {
        [IO.Directory]::Move($baseDir, (Join-Path $runtimeRoot ('old-base-' + [Guid]::NewGuid().ToString('N'))))
    }
    [IO.Directory]::Move($stage, $baseDir)
}

function Set-UpEnvironment {
    if (Test-Dependencies) { return }
    Install-BasePython
    Get-WheelCache
    # A venv copied with a project may still refer to the old base path.
    $ready = Test-Interpreter $envPython
    if ($ready) {
        $oldBase = & $envPython -I -X utf8 -c "import sys,sqlite3,pip; print(sys.base_prefix if sqlite3.sqlite_version == '3.50.4' and pip.__version__ == '26.2.2' else '')"
        $ready = ($LASTEXITCODE -eq 0 -and $oldBase -and [IO.Path]::GetFullPath($oldBase) -eq $baseDir)
    }
    if (-not $ready) {
        if ([IO.Directory]::Exists($envDir)) {
            [IO.Directory]::Move($envDir, (Join-Path $runtimeRoot ('old-venv-' + [Guid]::NewGuid().ToString('N'))))
        }
        Write-Host '正在创建项目虚拟环境 …'
        & $basePython -I -X utf8 -m venv $envDir
        if ($LASTEXITCODE -ne 0) { throw '创建 .venv 失败。请确认项目目录可写。' }
    }
    Write-Host '正在从本地缓存安装锁定的软件包 …'
    & $envPython -I -X utf8 -m pip --isolated --disable-pip-version-check install --no-index --no-cache-dir --no-compile --only-binary=:all: --require-hashes --find-links $wheelRoot -r $lockPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Dependencies -ExplainFailure)) { throw '依赖安装或环境校验失败。' }
}

$lockStream = $null
try {
    Set-Location -LiteralPath $projectRoot
    [IO.Directory]::CreateDirectory($wheelRoot) | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $lockFile = Join-Path $runtimeRoot 'setup.lock'
    $deadline = [DateTime]::UtcNow.AddMinutes(6)
    while (-not $lockStream) {
        try {
            $lockStream = [IO.File]::Open($lockFile, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            if ([DateTime]::UtcNow -gt $deadline) { throw '另一个启动器正在配置环境。请稍后重试。' }
            Start-Sleep -Milliseconds 350
        }
    }
    try { Set-UpEnvironment } finally { $lockStream.Dispose(); $lockStream = $null }
    if ($SetupOnly) { Write-Host "环境已就绪：$envPython"; exit 0 }
    $launchArgs = @('-m', 'opl', 'serve')
    if ($NoBrowser) { $launchArgs += '--no-browser' }
    & $envPython @launchArgs
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
