# YouTube Music ZimaOS - inicializador Windows (v1.5.5)
# Execute com duplo clique em iniciar-windows.bat. Nenhuma instalacao e silenciosa.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Compose = Join-Path $script:Root 'compose.yaml'
$script:DockerExe = $null
$script:Image = 'ghcr.io/lucassfontes/youtube-music-zimaos:1.5.5'
$script:HostPort = '8787'

function Info([string]$Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Ok([string]$Message) { Write-Host "[OK] $Message" -ForegroundColor Green }
function Warn([string]$Message) { Write-Host "[ATENCAO] $Message" -ForegroundColor Yellow }
function Ask([string]$Message) {
    $value = Read-Host "$Message [S/N]"
    return $value -match '^(s|sim|y|yes)$'
}
function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Set-HostPort {
    $port = '8787'
    $envFile = Join-Path $script:Root '.env'
    if (Test-Path $envFile) {
        foreach ($line in (Get-Content -LiteralPath $envFile)) {
            if ($line -match '^\s*APP_PORT\s*=\s*["'']?(\d+)["'']?\s*$') { $port = $Matches[1] }
        }
    }
    if ($env:APP_PORT) { $port = $env:APP_PORT }
    if ($port -notmatch '^\d{1,5}$' -or [int]$port -lt 1 -or [int]$port -gt 65535) {
        throw 'APP_PORT invalida em .env ou variavel de ambiente.'
    }
    $script:HostPort = $port
}
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ((@($machine, $user, $env:Path) | Where-Object { $_ }) -join ';')
}
function Find-Docker {
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $paths = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe')
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    return $null
}
function Start-DockerDesktop {
    Info 'Iniciando Docker Desktop...'
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { Start-Process -FilePath $p | Out-Null; return }
    }
    if ($script:DockerExe) {
        try { & $script:DockerExe desktop start; return } catch { }
    }
    Warn 'Abra o Docker Desktop manualmente pelo menu Iniciar do Windows.'
}
function Test-Wsl {
    $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    if (-not (Test-Path $wsl)) { return $false }
    try {
        $null = & $wsl --status 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function Install-Wsl {
    Warn 'WSL 2 nao esta pronto. O Windows pode pedir autorizacao de administrador e reinicializacao.'
    if (-not (Ask 'Deseja instalar/ativar WSL 2 agora?')) { throw 'Instalacao do WSL 2 recusada.' }
    $exit = 1
    if (Test-Admin) {
        & wsl.exe --install --no-distribution
        $exit = $LASTEXITCODE
    } else {
        $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'wsl.exe --install --no-distribution'
        )
        $exit = $proc.ExitCode
    }
    if ($exit -ne 0) { throw "Falha ao instalar WSL (codigo $exit). Veja: https://learn.microsoft.com/pt-br/windows/wsl/install" }
    Warn 'WSL foi configurado. Reinicie o Windows e execute iniciar-windows.bat novamente.'
    exit 20
}
function Check-Windows {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Este inicializador e somente para Windows.' }
    $arch = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if ($arch -ne 'AMD64') { throw 'Esta imagem Docker foi publicada apenas para computadores Windows x64 (AMD64).' }
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    if ($build -lt 19045) {
        throw "Windows build $build nao compativel com o Docker Desktop atual. Atualize para Windows 10 22H2 ou Windows 11 compativel."
    }
    try {
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $computer = Get-CimInstance Win32_ComputerSystem
        if (($cpu.VirtualizationFirmwareEnabled -eq $false) -and ($computer.HypervisorPresent -ne $true)) {
            throw 'Virtualizacao desativada. Ative Intel VT-x/AMD-V na BIOS/UEFI antes de instalar o Docker Desktop.'
        }
    } catch {
        if ($_.Exception.Message -match 'Virtualizacao desativada') { throw }
        Warn 'Nao foi possivel verificar a virtualizacao automaticamente.'
    }
    Ok "Windows compatível (build $build, arquitetura $arch)."
}
function Install-DockerDesktop {
    Warn 'Docker Desktop nao encontrado.'
    if (-not (Ask 'Deseja instalar Docker Desktop pela fonte oficial?')) { throw 'Instalacao do Docker recusada.' }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Info 'Instalando Docker Desktop com winget... (pode solicitar permissao de administrador)'
        & $winget.Source install --id Docker.DockerDesktop --exact --source winget --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { Ok 'Instalador do Docker Desktop concluido.'; return }
        Warn 'Instalacao via winget falhou; tentarei o instalador oficial com verificacao de integridade.'
    } else {
        Warn 'winget nao encontrado; usarei o instalador oficial verificado.'
    }
    # URL e SHA-256 fixados no manifesto Microsoft winget de 02/11/2026.
    # Atualize os dois juntos quando mudar a versao do instalador.
    $url = 'https://desktop.docker.com/win/main/amd64/245422/Docker%20Desktop%20Installer.exe'
    $expectedSha = 'CD6273D9EAA4F07FA7593FC6813D37E1004381CEBC3F26047A2554FA5DB24388'
    $file = Join-Path $env:TEMP 'Docker-Desktop-Installer-4.98.0.exe'
    Info 'Baixando o instalador oficial Docker Desktop 4.98.0...'
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    if ($hash -ne $expectedSha) { Remove-Item -LiteralPath $file -Force; throw 'Verificacao SHA-256 falhou. Instalacao cancelada por seguranca.' }
    Ok 'Integridade do instalador verificada.'
    Info 'Abrindo o instalador. Confirme as telas e os termos do Docker Desktop.'
    $p = Start-Process -FilePath $file -Verb RunAs -PassThru -Wait -ArgumentList @('install', '--backend=wsl-2', '--no-windows-containers')
    if ($p.ExitCode -ne 0) { throw "Instalador do Docker falhou (codigo $($p.ExitCode))." }
}
function Test-Engine {
    if (-not $script:DockerExe) { return $false }
    try {
        $out = & $script:DockerExe info --format '{{.OSType}}' 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        if ((@($out) -join " ").Trim() -eq 'windows') {
            throw 'Docker esta usando Windows Containers. Abra Docker Desktop e troque para Linux Containers.'
        }
        return ((@($out) -join " ").Trim() -eq 'linux')
    } catch {
        if ($_.Exception.Message -match 'Windows Containers') { throw }
        return $false
    }
}
function Wait-Engine {
    if (Test-Engine) { return }
    Start-DockerDesktop
    Warn 'A primeira inicializacao do Docker pode pedir aceite dos termos e/ou login no aplicativo.'
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 4
        Refresh-Path
        $script:DockerExe = Find-Docker
        if (Test-Engine) { Ok 'Docker Linux pronto.'; return }
        if (($i % 5) -eq 4) { Info 'Aguardando Docker Desktop iniciar...' }
    }
    throw 'Docker Desktop nao ficou pronto. Abra o aplicativo, conclua a configuracao/termos, e execute o inicializador novamente.'
}
function Docker-Compose([string[]]$ComposeArgs) {
    & $script:DockerExe compose -f $script:Compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose falhou (codigo $LASTEXITCODE)." }
}
function Local-Addresses {
    try {
        $ips = Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway -and $_.InterfaceAlias -notmatch 'Docker|vEthernet|WSL|Tailscale|VPN|ZeroTier|Virtual' }
        foreach ($if in $ips) {
            foreach ($address in $if.IPv4Address) {
                $ip = $address.IPAddress
                if ($ip -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') { Write-Host "  http://${ip}:$($script:HostPort)" -ForegroundColor White }
            }
        }
    } catch { }
}
function Launch-App {
    Info "Imagem Docker: $script:Image"
    Set-HostPort
    Info 'Criando pastas permanentes dentro do projeto...'
    foreach ($name in @('dados','musicas','videos')) {
        $folder = Join-Path $script:Root $name
        if (-not (Test-Path $folder)) { $null = New-Item -ItemType Directory -Force -Path $folder }
    }
    Info 'Baixando a imagem pronta. Nao sera necessario compilar Python/FFmpeg...'
    try {
        Docker-Compose @('pull')
    } catch {
        throw 'Nao consegui baixar a imagem do GHCR. Verifique a internet e se o pacote do GitHub esta publico (ou execute docker login ghcr.io). ' + $_.Exception.Message
    }
    Docker-Compose @('up', '-d', '--no-build')
    Ok 'YouTube Music ZimaOS iniciado!'
    Write-Host "  Neste computador: http://localhost:$($script:HostPort)" -ForegroundColor Green
    Info 'Outros aparelhos na mesma rede (se firewall permitir):'
    Local-Addresses
}
function Menu {
    while ($true) {
        Write-Host ''
        Write-Host '================ MENU DE CONTROLE ================' -ForegroundColor Cyan
        Write-Host ' 1 - Abrir aplicativo no navegador'
        Write-Host ' 2 - Ver ultimos logs'
        Write-Host ' 3 - Status do Docker'
        Write-Host ' 4 - Reiniciar aplicativo'
        Write-Host ' 5 - Parar aplicativo (sem apagar musicas)'
        Write-Host ' 6 - Atualizar a imagem e iniciar'
        Write-Host ' 0 - Sair e manter o container funcionando'
        $option = Read-Host 'Escolha uma opcao'
        try {
            switch ($option) {
                '1' { Start-Process "http://localhost:$($script:HostPort)" }
                '2' { Docker-Compose @('logs','--tail','100') }
                '3' { Docker-Compose @('ps') }
                '4' { Docker-Compose @('restart'); Ok 'Aplicativo reiniciado.' }
                '5' { Docker-Compose @('stop'); Ok 'Aplicativo parado. Dados permanecem nas pastas.' }
                '6' { Launch-App }
                '0' { Info 'Saindo do menu. Container permanece no estado atual.'; return }
                default { Warn 'Opcao desconhecida.' }
            }
        } catch { Warn $_.Exception.Message }
    }
}
try {
    if (-not (Test-Path $script:Compose)) { throw 'compose.yaml nao encontrado. Extraia todo o ZIP na mesma pasta.' }
    Check-Windows
    Refresh-Path
    $script:DockerExe = Find-Docker
    # Se o Docker Linux ja funciona com outro backend, nao force WSL sem necessidade.
    if (-not (Test-Engine) -and -not (Test-Wsl)) { Install-Wsl }
    if (-not $script:DockerExe) {
        Install-DockerDesktop
        Refresh-Path
        $script:DockerExe = Find-Docker
        if (-not $script:DockerExe) { throw 'Docker instalado, mas docker.exe ainda nao foi encontrado. Reinicie o Windows e execute o iniciador novamente.' }
    }
    Wait-Engine
    & $script:DockerExe compose version | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose V2 indisponivel. Atualize Docker Desktop.' }
    Launch-App
    Menu
} catch {
    Write-Host "[ERRO] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Se o Docker Desktop foi instalado agora, talvez seja necessario reiniciar o Windows.' -ForegroundColor Yellow
    exit 1
}
