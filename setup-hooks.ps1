#!/usr/bin/env pwsh
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("datadoghq.com", "datadoghq.eu", "ap1.datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "ddog-gov.com", "datad0g.com")]
    [string]$DdSite,

    [Parameter(Mandatory=$true)]
    [string]$AzDevOpsOrg,

    [switch]$Uninstall
)

$EVENT_TYPES = @(
    "git.pullrequest.created",
    "git.pullrequest.updated",
    "git.push",
    "ms.vss-pipelines.run-state-changed-event",
    "ms.vss-pipelines.stage-state-changed-event",
    "ms.vss-pipelines.job-state-changed-event",
    "ms.vss-pipelinechecks-events.approval-pending",
    "ms.vss-pipelinechecks-events.approval-completed",
    "build.complete"
)

function Write-Progress-Bar {
    param(
        [int]$Iteration,
        [int]$Total,
        [string]$Prefix = "",
        [string]$Suffix = "",
        [int]$Length = 30,
        [string]$Fill = "#"
    )

    $Percent = [math]::Round(100 * ($Iteration / [float]$Total), 1)
    $FilledLength = [math]::Floor($Length * $Iteration / $Total)
    $Bar = ($Fill * $FilledLength) + ("-" * ($Length - $FilledLength))
    Write-Host "`r$Prefix |$Bar| $Percent% $Suffix$(' ' * 30)" -NoNewline
}

class AzureDevOpsException : Exception {
    [string]$StatusCode
    [string]$ResponseText

    AzureDevOpsException([string]$Message, [object]$Response) : base($Message) {
        $this.StatusCode = $Response.StatusCode
        $this.ResponseText = $Response.Content
    }

    [string] ToString() {
        return "$($this.Message): $($this.StatusCode) $($this.ResponseText)"
    }
}

class Client {
    [string]$AzDevOpsToken
    [string]$AzDevOpsOrg
    [string]$DdSite
    [string]$DdApiKey
    [bool]$Verbose

    Client([string]$AzDevOpsOrg, [string]$AzDevOpsToken, [string]$DdSite, [string]$DdApiKey, [bool]$Verbose) {
        $this.AzDevOpsToken = $AzDevOpsToken
        $this.AzDevOpsOrg = $AzDevOpsOrg
        $this.DdSite = $DdSite
        $this.DdApiKey = $DdApiKey
        $this.Verbose = $Verbose
    }

    [object] GetAzAuthHeaders() {
        return @{
            "Authorization" = "Bearer $($this.AzDevOpsToken)"
        }
    }

    [string] GetAzBaseUrl() {
        return "https://dev.azure.com/$($this.AzDevOpsOrg)"
    }

    [string] GetWebhookUrl() {
        return "https://webhook-intake.$($this.DdSite)/api/v2/webhook"
    }

    [string] GetPublisherId([string]$EventType) {
        if ($EventType.StartsWith("ms.vss-pipelines.") -or $EventType.StartsWith("ms.vss-pipelinechecks-events.")) {
            return "pipelines"
        }
        else {
            return "tfs"
        }
    }

    [void] ValidateDdApiKey() {
        $url = "https://api.$($this.DdSite)/api/v1/validate"
        $headers = @{
            "DD-API-KEY" = $this.DdApiKey
        }

        try {
            $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Get
        }
        catch {
            throw "Invalid Datadog API key! Please check your Datadog site and API key.`n$($_.Exception.Response.StatusCode) $($_.Exception.Response.StatusDescription)"
        }
    }

    [array] ListProjects([string]$ContinuationToken = $null) {
        $url = "$($this.GetAzBaseUrl())/_apis/projects?api-version=7.1"
        $params = @{}
        if ($ContinuationToken) {
            $params["continuationToken"] = $ContinuationToken
        }

        try {
            $response = Invoke-RestMethod -Uri $url -Headers $this.GetAzAuthHeaders() -Method Get
        }
        catch {
            throw [AzureDevOpsException]::new("Error listing Azure DevOps projects", $_)
        }

        $projects = $response.value
        if ($response.continuationToken) {
            $projects += $this.ListProjects($response.continuationToken)
        }
        return $projects
    }

    [array] GetExistingHooks() {
        $url = "$($this.GetAzBaseUrl())/_apis/hooks/subscriptionsquery?api-version=7.1"
        $body = @{
            consumerId = "webHooks"
            consumerInputFilters = @(
                @{
                    conditions = @(
                        @{
                            inputId = "url"
                            inputValue = $this.GetWebhookUrl()
                            operator = "equals"
                        }
                    )
                }
            )
        }

        try {
            $response = Invoke-RestMethod -Uri $url -Headers $this.GetAzAuthHeaders() -Method Post -Body ($body | ConvertTo-Json -Depth 10) -ContentType "application/json"
        }
        catch {
            throw [AzureDevOpsException]::new("Error listing service hooks", $_)
        }

        return $response.results
    }

    [void] ConfigureServiceHook([object]$Project, [string]$EventType) {
        if ($this.Verbose) {
            Write-Host "Configuring $EventType service hook for project $($Project.name)..."
        }

        $url = "$($this.GetAzBaseUrl())/_apis/hooks/subscriptions?api-version=7.1"
        $publisherId = $this.GetPublisherId($EventType)
        $body = @{
            publisherId = $publisherId
            eventType = $EventType
            resourceVersion = "1.0"
            consumerId = "webHooks"
            consumerActionId = "httpRequest"
            publisherInputs = @{
                projectId = $Project.id
            }
            consumerInputs = @{
                url = $this.GetWebhookUrl()
                httpHeaders = "dd-api-key: $($this.DdApiKey)"
            }
        }

        try {
            $response = Invoke-RestMethod -Uri $url -Headers $this.GetAzAuthHeaders() -Method Post -Body ($body | ConvertTo-Json -Depth 10) -ContentType "application/json"
        }
        catch {
            throw [AzureDevOpsException]::new("Error configuring service hook for project $($Project.name)", $_)
        }
    }

    [void] DeleteServiceHook([object]$Hook) {
        if ($this.Verbose) {
            Write-Host "Removing $($Hook.eventType) service hook for project $($Hook.publisherInputs.projectId)..."
        }

        $url = "$($this.GetAzBaseUrl())/_apis/hooks/subscriptions/$($Hook.id)?api-version=7.1"

        try {
            $response = Invoke-RestMethod -Uri $url -Headers $this.GetAzAuthHeaders() -Method Delete
        }
        catch {
            throw [AzureDevOpsException]::new("Error deleting service hook $($Hook.id)", $_)
        }
    }

    [void] InstallHooks() {
        $projects = $this.ListProjects($null)

        if ($projects.Count -eq 0) {
            Write-Host "No projects found in $($this.AzDevOpsOrg)."
            return
        }

        $existingHooks = $this.GetExistingHooks()
        $existingHooksByProjectType = @{}
        foreach ($hook in $existingHooks) {
            $key = "$($hook.publisherInputs.projectId)_$($hook.eventType)"
            $existingHooksByProjectType[$key] = $hook
        }

        $toProcess = @()
        $numProjectsMissingAtLeastOne = 0
        foreach ($project in $projects) {
            $missingAtLeastOne = $false
            foreach ($eventType in $script:EVENT_TYPES) {
                $key = "$($project.id)_$eventType"
                if (-not $existingHooksByProjectType.ContainsKey($key)) {
                    $toProcess += @{
                        Project = $project
                        EventType = $eventType
                    }
                    $missingAtLeastOne = $true
                }
                elseif ($this.Verbose) {
                    Write-Host "$eventType service hook is already configured for project $($project.name)"
                }
            }

            if ($missingAtLeastOne) {
                $numProjectsMissingAtLeastOne++
            }
        }

        if ($toProcess.Count -eq 0) {
            Write-Host "All $($projects.Count) projects in $($this.AzDevOpsOrg) already have Datadog service hooks correctly configured!"
            return
        }

        $response = Read-Host "$numProjectsMissingAtLeastOne of $($projects.Count) projects in $($this.AzDevOpsOrg) are missing at least one service hook.`nPlease confirm that you want to configure service hooks for these $numProjectsMissingAtLeastOne projects (yes/no)"
        if ($response.ToLower() -notin @("yes", "y")) {
            Write-Host "Exiting."
            exit 1
        }

        for ($i = 0; $i -lt $toProcess.Count; $i++) {
            Write-Progress-Bar -Iteration ($i + 1) -Total $toProcess.Count -Prefix "Configuring service hooks" -Suffix "$($toProcess[$i].Project.name) - $($toProcess[$i].EventType)"
            $this.ConfigureServiceHook($toProcess[$i].Project, $toProcess[$i].EventType)
        }

        Write-Host "`nSuccessfully configured $($toProcess.Count) service hooks among $numProjectsMissingAtLeastOne projects in $($this.AzDevOpsOrg)!"
    }

    [void] UninstallHooks() {
        $hooks = $this.GetExistingHooks()
        if ($hooks.Count -eq 0) {
            Write-Host "No Datadog service hooks found."
            return
        }

        $projectIds = @{}
        foreach ($hook in $hooks) {
            $projectIds[$hook.publisherInputs.projectId] = $true
        }
        $projectCount = $projectIds.Count

        Write-Host "Found $($hooks.Count) Datadog service hooks among $projectCount projects in $($this.AzDevOpsOrg)."
        $response = Read-Host "Are you sure you want to uninstall these $projectCount Datadog service hooks? This will break the integration with Datadog. (yes/no)"
        if ($response.ToLower() -notin @("yes", "y")) {
            Write-Host "Exiting."
            exit 1
        }

        for ($i = 0; $i -lt $hooks.Count; $i++) {
            Write-Progress-Bar -Iteration ($i + 1) -Total $hooks.Count -Prefix "Uninstalling service hooks" -Suffix "$($hooks[$i].publisherInputs.projectId) - $($hooks[$i].eventType)"
            $this.DeleteServiceHook($hooks[$i])
        }

        Write-Host "`nSuccessfully uninstalled $($hooks.Count) Datadog service hooks among $projectCount projects in $($this.AzDevOpsOrg)!"
    }
}

$ddApiKey = $env:DD_API_KEY
if ([string]::IsNullOrEmpty($ddApiKey)) {
    Write-Host "DD_API_KEY is not set in your environment."
    exit 1
}

$azDevOpsToken = $env:AZURE_DEVOPS_TOKEN
if ([string]::IsNullOrEmpty($azDevOpsToken)) {
    Write-Host "AZURE_DEVOPS_TOKEN is not set in your environment."
    exit 1
}

$Verbose = $VerbosePreference -eq 'Continue'
$client = [Client]::new($AzDevOpsOrg, $azDevOpsToken, $DdSite, $ddApiKey, $Verbose)
$client.ValidateDdApiKey()

try {
    if ($Uninstall) {
        $client.UninstallHooks()
    }
    else {
        $client.InstallHooks()
    }
}
catch [AzureDevOpsException] {
    $e = $_
    if ($e.StatusCode -in @(401, 403, 203)) {
        Write-Host "Invalid Azure DevOps token! Please check that your Azure DevOps token is valid and has admin access to the organization."
    }
    else {
        Write-Host "$($e.StatusCode) error from Azure DevOps API: $($e.ResponseText)"
    }
    exit 1
}
