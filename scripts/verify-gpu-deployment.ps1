[CmdletBinding()]
param(
    [ValidateSet("Compose", "Kubernetes", "All")]
    [string]$Mode = "All",
    [string]$ComposeFile = "docker-compose.microservices.yml",
    [string]$ComposeProject = "repo-search-gpu-verification",
    [string]$EnvFile = "",
    [string]$Namespace = "repo-search",
    [string]$MinikubeProfile = "repo-search"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$CheckName
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $Executable @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "$CheckName nije uspeo (exit code $exitCode).`n$($output | Out-String)"
    }
    return ($output | Out-String).Trim()
}

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Write-Check {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[GPU PROVERA] $Message"
}

function Get-ComposeArguments {
    $arguments = @("compose", "-p", $ComposeProject)
    if ($EnvFile) {
        $arguments += @("--env-file", $EnvFile)
    }
    return $arguments + @("-f", $ComposeFile)
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$CheckName
    )
    return Invoke-Checked -Executable "docker" -Arguments ((Get-ComposeArguments) + $Arguments) -CheckName $CheckName
}

$embeddingStatusCode = @'
import json, os, urllib.request
request = urllib.request.Request(
    "http://localhost:8000/model/status",
    headers={"X-API-Key": os.environ["API_TOKEN"]},
)
payload = json.loads(urllib.request.urlopen(request, timeout=30).read())
assert payload["embedding_device"] == "cuda", payload
assert payload["embedding_gpu_required"] is True, payload
assert payload["embedding_initialization_error"] is None, payload
print(json.dumps(payload, sort_keys=True))
'@

$embeddingRequestCode = @'
import json, os, urllib.request
body = json.dumps({"query": "GPU verification"}).encode()
request = urllib.request.Request(
    "http://localhost:8000/embed/query",
    data=body,
    headers={"Content-Type": "application/json", "X-API-Key": os.environ["API_TOKEN"]},
)
payload = json.loads(urllib.request.urlopen(request, timeout=120).read())
assert len(payload["embedding"]) == 1024, len(payload["embedding"])
print("embedding_dimension=1024")
'@

$cudaEvidenceCode = @'
import json, os, torch
assert torch.cuda.is_available(), "torch.cuda.is_available() je false"
visible = os.environ.get("NVIDIA_VISIBLE_DEVICES", "")
assert visible and visible.lower() not in {"none", "void"}, visible
print(json.dumps({"cuda": True, "gpu": torch.cuda.get_device_name(0), "visible_devices": visible}))
'@

function ConvertTo-PythonCommand {
    param([Parameter(Mandatory = $true)][string]$Code)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
    return "import base64;exec(base64.b64decode('$encoded'))"
}

$embeddingStatusCommand = ConvertTo-PythonCommand -Code $embeddingStatusCode
$embeddingRequestCommand = ConvertTo-PythonCommand -Code $embeddingRequestCode
$cudaEvidenceCommand = ConvertTo-PythonCommand -Code $cudaEvidenceCode

function Test-ComposeGpu {
    Write-Check "Docker Compose GPU konfiguracija"
    [void](Invoke-Compose -Arguments @("config", "--quiet") -CheckName "Compose config")

    foreach ($service in @("embedding-service", "ollama")) {
        $containerId = Invoke-Compose -Arguments @("ps", "-q", $service) -CheckName "$service container ID"
        Assert-True -Condition ([bool]$containerId) -Message "$service kontejner nije pokrenut."
        $inspection = (Invoke-Checked -Executable "docker" -Arguments @("inspect", $containerId) -CheckName "$service GPU inspect") | ConvertFrom-Json
        $requests = @($inspection[0].HostConfig.DeviceRequests)
        $hasGpuRequest = $false
        foreach ($request in $requests) {
            $capabilitySets = @($request.Capabilities)
            if (($capabilitySets | ConvertTo-Json -Compress) -match "gpu") {
                $hasGpuRequest = $true
            }
        }
        Assert-True -Condition $hasGpuRequest -Message "$service nije dobio Docker GPU device request."
        Write-Check "$service je dobio GPU device request"
    }

    $status = Invoke-Compose -Arguments @("exec", "-T", "embedding-service", "python", "-c", $embeddingStatusCommand) -CheckName "Compose embedding model status"
    Write-Check "Embedding Service model status: $status"
    $cuda = Invoke-Compose -Arguments @("exec", "-T", "embedding-service", "python", "-c", $cudaEvidenceCommand) -CheckName "Compose CUDA dokaz"
    Write-Check "Embedding Service CUDA dokaz: $cuda"
    $embedding = Invoke-Compose -Arguments @("exec", "-T", "embedding-service", "python", "-c", $embeddingRequestCommand) -CheckName "Compose embedding zahtev"
    Write-Check $embedding

    $models = Invoke-Compose -Arguments @("exec", "-T", "ollama", "ollama", "list") -CheckName "Compose Ollama modeli"
    Assert-True -Condition ($models -match "gemma4:12b") -Message "gemma4:12b nije dostupan u Compose Ollama servisu."
    $inference = Invoke-Compose -Arguments @("exec", "-T", "ollama", "ollama", "run", "gemma4:12b", "Return only: ok") -CheckName "Compose Ollama inference"
    Assert-True -Condition ([bool]$inference) -Message "Ollama inference nije vratio odgovor."
    $processor = Invoke-Compose -Arguments @("exec", "-T", "ollama", "ollama", "ps") -CheckName "Compose Ollama GPU proces"
    Assert-True -Condition (($processor -match "GPU") -and ($processor -notmatch "100% CPU")) -Message "ollama ps ne potvrđuje GPU izvršavanje.`n$processor"
    Write-Check "Ollama GPU dokaz: $processor"

    foreach ($service in @("embedding-service", "ollama")) {
        $containerId = Invoke-Compose -Arguments @("ps", "-q", $service) -CheckName "$service završni container ID"
        $health = Invoke-Checked -Executable "docker" -Arguments @("inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", $containerId) -CheckName "$service health"
        Assert-True -Condition ($health -eq "healthy") -Message "$service nije healthy posle GPU provere: $health"
    }
    Write-Check "Oba Compose servisa su healthy posle stvarnih GPU zahteva"
}

function Test-KubernetesGpu {
    Write-Check "Kubernetes GPU konfiguracija"
    $nodeJson = Invoke-Checked -Executable "kubectl" -Arguments @("get", "node", $MinikubeProfile, "-o", "json") -CheckName "Minikube node"
    $node = $nodeJson | ConvertFrom-Json
    $capacity = [int]$node.status.capacity."nvidia.com/gpu"
    $allocatable = [int]$node.status.allocatable."nvidia.com/gpu"
    Assert-True -Condition (($capacity -ge 1) -and ($allocatable -ge 1)) -Message "Minikube node ne izlaže zdrav nvidia.com/gpu resurs."
    $nodeGpu = Invoke-Checked -Executable "minikube" -Arguments @("ssh", "-p", $MinikubeProfile, "--", "nvidia-smi --query-gpu=name,uuid --format=csv,noheader") -CheckName "Minikube nvidia-smi"
    Write-Check "Minikube GPU: $nodeGpu"

    $runtime = Invoke-Checked -Executable "kubectl" -Arguments @("get", "runtimeclass", "nvidia", "-o", "json") -CheckName "NVIDIA RuntimeClass"
    Assert-True -Condition ((($runtime | ConvertFrom-Json).handler) -eq "nvidia") -Message "RuntimeClass nvidia nema handler nvidia."

    [void](Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "wait", "--for=condition=available", "deployment/embedding-service", "--timeout=600s") -CheckName "Embedding Service rollout")
    [void](Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "wait", "--for=condition=available", "deployment/ollama", "--timeout=600s") -CheckName "Ollama rollout")

    foreach ($app in @("embedding-service", "ollama")) {
        $pods = (Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "get", "pods", "-l", "app=$app", "-o", "json") -CheckName "$app pods") | ConvertFrom-Json
        $running = @($pods.items | Where-Object { $_.status.phase -eq "Running" -and $_.spec.nodeName })
        Assert-True -Condition ($running.Count -eq 1) -Message "$app nema tačno jedan zakazan Running pod."
        Write-Check "$app je zakazan na node-u $($running[0].spec.nodeName)"
    }

    $status = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/embedding-service", "--", "python", "-c", $embeddingStatusCommand) -CheckName "Kubernetes embedding model status"
    Write-Check "Embedding Service model status: $status"
    $cuda = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/embedding-service", "--", "python", "-c", $cudaEvidenceCommand) -CheckName "Kubernetes CUDA dokaz"
    Write-Check "Embedding Service CUDA dokaz: $cuda"
    $embedding = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/embedding-service", "--", "python", "-c", $embeddingRequestCommand) -CheckName "Kubernetes embedding zahtev"
    Write-Check $embedding

    $ollamaVisible = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/ollama", "--", "printenv", "NVIDIA_VISIBLE_DEVICES") -CheckName "Ollama visible devices"
    Assert-True -Condition ($ollamaVisible -eq "all") -Message "Ollama NVIDIA_VISIBLE_DEVICES nije all: $ollamaVisible"
    $models = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/ollama", "--", "ollama", "list") -CheckName "Kubernetes Ollama modeli"
    Assert-True -Condition ($models -match "gemma4:12b") -Message "gemma4:12b nije dostupan u Kubernetes Ollama podu."
    $inference = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/ollama", "--", "ollama", "run", "gemma4:12b", "Return only: ok") -CheckName "Kubernetes Ollama inference"
    Assert-True -Condition ([bool]$inference) -Message "Ollama inference nije vratio odgovor."
    $processor = Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "exec", "deployment/ollama", "--", "ollama", "ps") -CheckName "Kubernetes Ollama GPU proces"
    Assert-True -Condition (($processor -match "GPU") -and ($processor -notmatch "100% CPU")) -Message "ollama ps ne potvrđuje GPU izvršavanje.`n$processor"
    Write-Check "Ollama GPU dokaz: $processor"

    [void](Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "rollout", "status", "daemonset/dcgm-exporter", "--timeout=300s") -CheckName "DCGM exporter rollout")
    $metrics = Invoke-Checked -Executable "kubectl" -Arguments @("get", "--raw", "/api/v1/namespaces/$Namespace/services/http:dcgm-exporter:9400/proxy/metrics") -CheckName "DCGM metrics endpoint"
    Assert-True -Condition ($metrics -match "DCGM_FI_DEV_GPU_UTIL") -Message "DCGM endpoint ne izlaže GPU utilization metriku."

    $prometheusObserved = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        try {
            $query = Invoke-Checked -Executable "kubectl" -Arguments @("get", "--raw", "/api/v1/namespaces/$Namespace/services/http:prometheus:9090/proxy/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL") -CheckName "Prometheus DCGM query"
            $payload = $query | ConvertFrom-Json
            if ($payload.status -eq "success" -and @($payload.data.result).Count -gt 0) {
                $prometheusObserved = $true
                break
            }
        }
        catch {
            if ($attempt -eq 12) { throw }
        }
        Start-Sleep -Seconds 5
    }
    Assert-True -Condition $prometheusObserved -Message "Prometheus nije prikupio DCGM GPU metrike."

    foreach ($deployment in @("embedding-service", "ollama")) {
        [void](Invoke-Checked -Executable "kubectl" -Arguments @("-n", $Namespace, "wait", "--for=condition=available", "deployment/$deployment", "--timeout=60s") -CheckName "$deployment završna spremnost")
    }
    Write-Check "Embedding Service i Ollama su istovremeno spremni posle stvarnih GPU zahteva"
}

try {
    if ($Mode -in @("Compose", "All")) {
        Test-ComposeGpu
    }
    if ($Mode -in @("Kubernetes", "All")) {
        Test-KubernetesGpu
    }
    Write-Host "GPU verifikacija je uspešno završena za režim: $Mode"
    exit 0
}
catch {
    Write-Error "GPU verifikacija nije uspela: $($_.Exception.Message)"
    exit 1
}
