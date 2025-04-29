# Azure DevOps service hooks setup for Source Code Integration

This script is a helper to configure service hooks required by Datadog's Source Code Integration.

Datadog requires the following event types:
- [`git.pullrequest.created`](https://learn.microsoft.com/en-us/azure/devops/service-hooks/events?view=azure-devops#git.pullrequest.created)
- [`git.pullrequest.updated`](https://learn.microsoft.com/en-us/azure/devops/service-hooks/events?view=azure-devops#git.pullrequest.updated)
- [`git.push`](https://learn.microsoft.com/en-us/azure/devops/service-hooks/events?view=azure-devops#git.push)

## Usage

This script requires a working Python 3 installation, with the `requests` module installed.

This script requires an Azure DevOps Personal Access Token to query Azure DevOps APIs on your behalf. You can refer to [this Microsoft documentation](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate?view=azure-devops&tabs=Windows#create-a-pat) for instructions on how to generate one.

```sh
export AZURE_DEVOPS_TOKEN=<secret token>
```

### Installing the service hooks

To install the service hooks on all projects in your Azure DevOps organization, you'll need:
- Your Datadog site parameter ([learn more](https://docs.datadoghq.com/getting_started/site/#access-the-datadog-site))
- A Datadog API key. This will allow authenticating the webhooks that Azure DevOps sends to Datadog on behalf of your organization. Please refer to [this Datadog documentation](https://docs.datadoghq.com/account_management/api-app-keys/) to generate an API key.
- Your Azure DevOps organization slug. This is the first path segment in a repository URL (e.g. `my-org` for `dev.azure.com/my-org/...`)

The command will display the number of affected projects and prompt you for confirmation before proceeding.

```sh
DD_API_KEY=<api-key> ./setup-hooks.py --dd-site=<datadog-site> --az-devops-org=<organization-slug>
```

If you only want to install the service hooks for a single project in your Azure DevOps organization, you can use the `--project` flag to only target this one.

### Uninstalling the service hooks

The following command will uninstall the Datadog service hooks from **all projects** in your organization:

```sh
./setup-hooks.py --dd-site=<datadog-site> --az-devops-org=<organization-slug> --uninstall
```

## Limitations

- This script currently only supports installing service hooks on an Azure DevOps organization for a single Datadog organization. Configuring the same Azure DevOps organization for multiple Datadog organizations is not supported.
