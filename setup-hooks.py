#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# The event types that Datadog's Source Code Integration requires
EVENT_TYPES = [
    "git.pullrequest.created",
    "git.pullrequest.updated",
    "git.push",
    "ms.vss-pipelines.run-state-changed-event",
    "ms.vss-pipelines.stage-state-changed-event",
    "ms.vss-pipelines.job-state-changed-event",
    "ms.vss-pipelinechecks-events.approval-pending",
    "ms.vss-pipelinechecks-events.approval-completed",
    "build.complete",
]

# Resource Versions
VERSION_1_0 = "1.0"

# Default resource version for event types
DEFAULT_RESOURCE_VERSION = "latest"

# Mapping of event types to their resource versions
EVENT_TYPE_VERSIONS = {
    "git.pullrequest.created": VERSION_1_0,
    "git.pullrequest.updated": VERSION_1_0,
    "git.push": VERSION_1_0,
}

VALID_DD_SITES = [
    "datadoghq.com",
    "datadoghq.eu",
    "ap1.datadoghq.com",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "datad0g.com",
]


def main():
    parser = argparse.ArgumentParser(
        description="Configure Datadog service hooks for Azure DevOps projects",
        epilog="""
        DD_API_KEY must be set in your environment, and contain a valid Datadog API key for the site you are using.
        AZURE_DEVOPS_TOKEN must be set in your environment, and contain an Azure DevOps personal access token with admin access to the organization you are using.
        """,
    )
    parser.add_argument(
        "--dd-site",
        type=str,
        help="Datadog site to use",
        choices=VALID_DD_SITES,
    )
    parser.add_argument(
        "-o",
        "--az-devops-org",
        type=str,
        help="Azure DevOps organization on which service hooks will be configured, The path segment after dev.azure.com/ in your organization URL.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Additional logging for every API call that is performed",
        default=False,
        action="store_true",
    )
    parser.add_argument(
        "--uninstall",
        help="Uninstall Datadog service hooks from all projects in the organization",
        default=False,
        action="store_true",
    )
    parser.add_argument(
        "--project",
        help="Specify a project to interact with. This will scope the installation to a single project in your Azure DevOps organization.",
        type=str,
    )

    args = parser.parse_args()

    # Check env vars are set correctly
    az_devops_token = os.getenv("AZURE_DEVOPS_TOKEN")
    if az_devops_token is None or az_devops_token == "":
        print("AZURE_DEVOPS_TOKEN is not set in your environment.")
        exit(1)

    dd_api_key = os.getenv("DD_API_KEY")
    # we only need the DD_API_KEY to install, not uninstall
    if not args.uninstall and (dd_api_key is None or dd_api_key == ""):
        print("DD_API_KEY is not set in your environment.")
        exit(1)

    client = Client(
        args.az_devops_org,
        az_devops_token,
        args.dd_site,
        dd_api_key,
        args.verbose,
        args.project,
    )

    try:
        if args.uninstall:
            client.uninstall_hooks()
        else:
            client.install_hooks()

    except AzureDevOpsException as e:
        if (
            e.response.status_code == 401
            or e.response.status_code == 403
            or e.response.status_code == 203  # 203 is used for the login redirect
        ):
            print(
                "Invalid Azure DevOps token! Please check that your Azure DevOps token is valid and has admin access to the organization."
            )
        else:
            print(
                f"{e.response.status_code} error from Azure DevOps API: {e.response.text}"
            )
        exit(1)


class Client:
    def __init__(
        self,
        az_devops_org,
        az_devops_token,
        dd_site,
        dd_api_key,
        verbose=True,
        project=None,
    ):
        self.az_devops_token = az_devops_token
        self.az_devops_org = az_devops_org
        self.dd_site = dd_site
        self.dd_api_key = dd_api_key
        self.verbose = verbose
        self.project = project

    def install_hooks(self):
        self.validate_dd_api_key()

        projects = self.list_projects()

        if self.project is not None:
            projects = [p for p in projects if p["name"] == self.project]

        if len(projects) == 0:
            if self.project is None:
                print(f"No projects found in {self.az_devops_org}.")
            else:
                print(f"Project {self.project} not found in {self.az_devops_org}")
            return

        existing_hooks_by_project_type = {
            (hook["publisherInputs"]["projectId"], hook["eventType"]): hook
            for hook in self.get_existing_hooks()
        }

        toProcess = []
        numProjectsMissingAtLeastOne = 0
        for project in projects:
            missingAtLeastOne = False
            for event_type in EVENT_TYPES:
                if (project["id"], event_type) not in existing_hooks_by_project_type:
                    toProcess += [(project, event_type)]
                    missingAtLeastOne = True
                else:
                    self.verbose_print(
                        f"{event_type} service hook is already configured for project {project['name']}"
                    )

            if missingAtLeastOne:
                numProjectsMissingAtLeastOne += 1

        if len(toProcess) == 0:
            if self.project is None:
                print(
                    f"All {len(projects)} projects in {self.az_devops_org} already have Datadog service hooks correctly configured!"
                )
            else:
                print(
                    f"The project {self.project} already has Datadog service hooks correctly configured!"
                )
            return

        # Prompt confirmation for batch setup
        if self.project is None:
            yesno = input(
                f"{numProjectsMissingAtLeastOne} of {len(projects)} projects in {self.az_devops_org} are missing at least one service hook.\nPlease confirm that you want to configure service hooks for these {numProjectsMissingAtLeastOne} projects (yes/no): "
            )
            if yesno.lower() not in ["yes", "y"]:
                print("Exiting.")
                exit(1)

        for i, (project, event_type) in enumerate(toProcess):
            progress(
                i + 1,
                len(toProcess),
                prefix="Configuring service hooks",
                suffix=f"{project['name']} - {event_type}",
            )
            self.configure_service_hook(project, event_type)

        if self.project is None:
            print(
                f"\nSuccessfully configured {len(toProcess)} service hooks among {numProjectsMissingAtLeastOne} projects in {self.az_devops_org}!"
            )
        else:
            print(
                f"\nSuccessfully configured {len(toProcess)} service hooks in project {self.project}!"
            )

    def uninstall_hooks(self):
        if self.project is not None:
            print(
                "Specifying a single project is not supported for the uninstallation command."
            )
            return

        hooks = self.get_existing_hooks()

        if len(hooks) == 0:
            print("No Datadog service hooks found.")
            return

        project_count = len({hook["publisherInputs"]["projectId"] for hook in hooks})
        print(
            f"Found {len(hooks)} Datadog service hooks among {project_count} projects in {self.az_devops_org}."
        )
        yesno = input(
            f"Are you sure you want to uninstall these {project_count} Datadog service hooks ? This will break the integration with Datadog. (yes/no): "
        )
        if yesno.lower() not in ["yes", "y"]:
            print("Exiting.")
            exit(1)

        for i, hook in enumerate(hooks):
            progress(
                i + 1,
                len(hooks),
                prefix="Uninstalling service hooks",
                suffix=f"{hook['publisherInputs']['projectId']} - {hook['eventType']}",
            )
            self.delete_service_hook(hook)

        print(
            f"\nSuccessfully uninstalled {len(hooks)} Datadog service hooks among {project_count} projects in {self.az_devops_org}!"
        )

    def list_projects(self, continuation_token=None):
        base_url = f"{self._az_base_url()}/_apis/projects"
        params = {"api-version": "7.1"}
        if continuation_token:
            params = {"continuationToken": continuation_token}

        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=self._az_auth_headers())
        try:
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    raise AzureDevOpsException(
                        "Error listing Azure DevOps projects", response
                    )
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            raise AzureDevOpsException("Error listing Azure DevOps projects", e) from e
        projects = data["value"]
        continuation_token = data.get("continuation_token")
        if continuation_token:
            projects += self.list_projects(continuation_token)
        return projects

    def get_existing_hooks(self):
        url = f"{self._az_base_url()}/_apis/hooks/subscriptionsquery?api-version=7.1"
        payload = json.dumps(
            {
                "consumerId": "webHooks",
                "consumerInputFilters": [
                    {
                        "conditions": [
                            {
                                "inputId": "url",
                                "inputValue": self._webhook_url(),
                                "operator": "equals",
                            }
                        ]
                    }
                ],
            }
        ).encode("utf-8")
        headers = self._az_auth_headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=headers, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    raise AzureDevOpsException("Error listing service hooks", response)
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            raise AzureDevOpsException("Error listing service hooks", e) from e
        return data["results"]

    def _get_publisher_id(self, event_type):
        """Get the correct publisher ID for a given event type."""
        if event_type.startswith("ms.vss-pipelines.") or event_type.startswith("ms.vss-pipelinechecks-events."):
            return "pipelines"
        else:
            return "tfs"

    def configure_service_hook(self, project, event_type):
        self.verbose_print(
            f"Configuring {event_type} service hook for project {project['name']}..."
        )
        url = f"{self._az_base_url()}/_apis/hooks/subscriptions?api-version=7.1"
        publisher_id = self._get_publisher_id(event_type)
        resource_version = EVENT_TYPE_VERSIONS.get(event_type, DEFAULT_RESOURCE_VERSION)
        payload = json.dumps(
            {
                "publisherId": publisher_id,
                "eventType": event_type,
                "resourceVersion": resource_version,
                "consumerId": "webHooks",
                "consumerActionId": "httpRequest",
                "publisherInputs": {
                    "projectId": project["id"],
                },
                "consumerInputs": {
                    "url": self._webhook_url(),
                    "httpHeaders": "dd-api-key: " + self.dd_api_key,
                },
            }
        ).encode("utf-8")
        headers = self._az_auth_headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=headers, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    raise AzureDevOpsException(
                        f"Error configuring service hook for project {project['name']}",
                        response,
                    )
        except urllib.error.HTTPError as e:
            raise AzureDevOpsException(
                f"Error configuring service hook for project {project['name']}",
                e,
            ) from e

    def delete_service_hook(self, hook):
        self.verbose_print(
            f"Removing {hook['eventType']} service hook for project {hook['publisherInputs']['projectId']}..."
        )
        url = f"{self._az_base_url()}/_apis/hooks/subscriptions/{hook['id']}?api-version=7.1"
        req = urllib.request.Request(
            url, headers=self._az_auth_headers(), method="DELETE"
        )
        try:
            with urllib.request.urlopen(req) as response:
                if response.status != 204:
                    raise AzureDevOpsException(
                        f"Error deleting service hook {hook['id']}", response
                    )
        except urllib.error.HTTPError as e:
            if e.code != 204:
                raise AzureDevOpsException(
                    f"Error deleting service hook {hook['id']}", e
                ) from e

    def _az_auth_headers(self):
        return {"Authorization": f"Bearer {self.az_devops_token}"}

    def _az_base_url(self):
        return f"https://dev.azure.com/{self.az_devops_org}"

    def _webhook_url(self):
        return f"https://webhook-intake.{self.dd_site}/api/v2/webhook"

    def validate_dd_api_key(self):
        url = f"https://api.{self.dd_site}/api/v1/validate"
        req = urllib.request.Request(url, headers={"DD-API-KEY": self.dd_api_key})
        try:
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    raise Exception(
                        f"Invalid Datadog API key! Please check your Datadog site and API key.\n{response.status} {response.read().decode()}"
                    )
        except urllib.error.HTTPError as e:
            raise Exception(
                f"Error validating Datadog API key! \n{e.code} {e.read().decode()}"
            ) from e

    def verbose_print(self, *args, **kwargs):
        if self.verbose:
            print(*args, **kwargs)


class AzureDevOpsException(Exception):
    def __init__(self, message, response):
        self.message = message
        self.response = response

    def __str__(self):
        return f"{self.message}: {self.response.status_code} {self.response.text}"


def progress(iteration, total, prefix="", suffix="", length=30, fill="#"):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + "-" * (length - filled_length)
    sys.stdout.write(f"\r{prefix} |{bar}| {percent}% {suffix}{' ' * 30}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
