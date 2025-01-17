import sys
import time
from functools import partial

from requests import get, post, delete, put
import argparse

from tenacity import retry, stop_after_delay, wait_fixed, retry_if_exception_type, stop_after_attempt

IGNORED_BRANCHES = ["main", "master"]


class OctopusApiError(Exception):
    pass


# Define shorthand decorator for the used settings.
retry_on_communication_error = partial(
    retry,
    stop=stop_after_delay(60) | stop_after_attempt(3),  # max. 60 seconds wait.
    wait=wait_fixed(0.4),  # wait 400ms
    retry=retry_if_exception_type(OctopusApiError),
)()


def is_not_blank(s):
    return bool(s and not s.isspace())


def is_blank(s):
    return not is_not_blank(s)


def parse_args():
    parser = argparse.ArgumentParser(description='Manage feature branches in Octopus.')
    parser.add_argument('--action', dest='action', action='store', help='create or delete.',
                        required=True)
    parser.add_argument('--octopusUrl', dest='octopus_url', action='store', help='The Octopus server URL.',
                        required=True)
    parser.add_argument('--octopusApiKey', dest='octopus_api_key', action='store', help='The Octopus API key',
                        required=True)
    parser.add_argument('--octopusSpace', dest='octopus_space', action='store', help='The Octopus space.',
                        required=True)
    parser.add_argument('--octopusProject', dest='octopus_project', action='store',
                        help='A comma separated list of Octopus projects', required=True)
    parser.add_argument('--branchName', dest='branch_name', action='store', help='The Octopus environment.',
                        required=True)
    parser.add_argument('--deploymentStepName', dest='deployment_step_name', action='store',
                        help='The name of the step that deploys the packages. '
                             + 'Leave blank to apply default rules to all steps with packages.', required=False)
    parser.add_argument('--deploymentPackageName', dest='deployment_package_name', action='store',
                        help='The name of the package deployed in the step defined in deploymentStepName.',
                        required=False)
    parser.add_argument('--targetName', dest='target_name', action='store',
                        help='Targets with this name are (un)assigned to the new environment. ' +
                             'Target name takes precedence over target role if both are specified.',
                        required=False)
    parser.add_argument('--targetRole', dest='target_role', action='store',
                        help='Targets with this role are (un)assigned to the new environment.',
                        required=False)
    parser.add_argument('--targetEnvironment', dest='target_environment', action='store',
                        help='Targets assigned to this environment and the role passed in via --targetRole '
                             + 'are (un)assigned to the new environment.',
                        required=False)

    return parser.parse_args()


def build_headers():
    return {"X-Octopus-ApiKey": args.octopus_api_key}


def get_space_id(space_name):
    if is_blank(space_name):
        return None

    url = args.octopus_url + "/api/spaces?partialName=" + space_name.strip() + "&take=1000"
    response = get(url, headers=headers)
    spaces_json = response.json()

    filtered_items = [a for a in spaces_json["Items"] if a["Name"] == space_name.strip()]

    if len(filtered_items) == 0:
        # Check to see if the space name was actually a space ID
        url = args.octopus_url + "/api/spaces/" + space_name
        response = get(url, headers=headers)
        if not response:
            sys.stderr.write("The space called " + space_name + " could not be found.\n")
            return None

        # A valid response means the space name was a valid ID
        return space_name

    first_id = filtered_items[0]["Id"]
    return first_id


def get_resource_id(space_id, resource_type, resource_name):
    if is_blank(space_id) or is_blank(resource_type) or is_blank(resource_name):
        return None

    url = args.octopus_url + "/api/" + space_id + "/" + resource_type + "?partialName=" \
          + resource_name.strip() + "&take=1000"
    response = get(url, headers=headers)
    json = response.json()

    filtered_items = [a for a in json["Items"] if a["Name"] == resource_name.strip()]
    if len(filtered_items) == 0:
        sys.stderr.write("The resource called " + resource_name + " of type " + resource_type
                         + " could not be found in space " + space_id + ".\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


def get_resource(space_id, resource_type, resource_id):
    if is_blank(space_id) or is_blank(resource_type) or is_blank(resource_id):
        return None

    url = args.octopus_url + "/api/" + space_id + "/" + resource_type + "/" + resource_id
    response = get(url, headers=headers)
    json = response.json()

    return json


def create_environment(space_id, branch_name):
    if is_blank(space_id) or is_blank(branch_name):
        return None

    environment_id = get_resource_id(space_id, "environments", branch_name)

    if environment_id is not None:
        sys.stderr.write("Found environment " + environment_id + "\n")
        return environment_id

    environment = {
        'Name': branch_name
    }
    url = args.octopus_url + "/api/" + space_id + "/environments"
    response = post(url, headers=headers, json=environment)
    if not response:
        raise OctopusApiError
    json = response.json()
    sys.stderr.write("Created environment " + json["Id"] + "\n")
    return json["Id"]


def create_lifecycle(space_id, environment_id, branch_name):
    if is_blank(space_id) or is_blank(environment_id) or is_blank(branch_name):
        return None

    lifecycle_id = get_resource_id(space_id, "lifecycles", branch_name)

    if lifecycle_id is not None:
        sys.stderr.write("Found lifecycle " + lifecycle_id + "\n")
        return lifecycle_id

    lifecycle = {
        'Id': None,
        'Name': branch_name,
        'SpaceId': space_id,
        'Phases': [{
            'Name': branch_name,
            'OptionalDeploymentTargets': [environment_id],
            'AutomaticDeploymentTargets': [],
            'MinimumEnvironmentsBeforePromotion': 0,
            'IsOptionalPhase': False
        }],
        'ReleaseRetentionPolicy': {
            'ShouldKeepForever': True,
            'QuantityToKeep': 0,
            'Unit': 'Days'
        },
        'TentacleRetentionPolicy': {
            'ShouldKeepForever': True,
            'QuantityToKeep': 0,
            'Unit': 'Days'
        },
        'Links': None
    }

    url = args.octopus_url + "/api/" + space_id + "/lifecycles"
    response = post(url, headers=headers, json=lifecycle)
    if not response:
        raise OctopusApiError
    json = response.json()
    sys.stderr.write("Created lifecycle " + json["Id"] + "\n")
    return json["Id"]


def find_channel(space_id, project_id, branch_name):
    if is_blank(space_id) or is_blank(project_id) or is_blank(branch_name):
        return None

    url = args.octopus_url + "/api/" + space_id + "/projects/" + project_id + "/channels?partialName=" \
          + branch_name.strip() + "&take=1000"
    response = get(url, headers=headers)
    json = response.json()

    filtered_items = [a for a in json["Items"] if a["Name"] == branch_name.strip()]
    if len(filtered_items) == 0:
        sys.stderr.write("The resource called " + branch_name + " of type channel could not be found in space "
                         + space_id + ".\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


def find_targets(space_id):
    if is_blank(space_id):
        return None

    url = args.octopus_url + "/api/" + space_id + "/machines?take=1000"
    response = get(url, headers=headers)

    if not response:
        raise OctopusApiError

    json = response.json()
    return json["Items"]


def find_targets_by_role(space_id, role_name):
    if is_blank(space_id) or is_blank(role_name):
        return None

    url = args.octopus_url + "/api/" + space_id + "/machines?take=1000"
    response = get(url, headers=headers)

    if not response:
        raise OctopusApiError

    json = response.json()
    return [a for a in json["Items"] if role_name in a["Roles"]]


def find_packages(space_id, project_id):
    if is_blank(space_id) or is_blank(project_id):
        return None

    url = args.octopus_url + "/api/" + space_id + "/projects/" + project_id + "/deploymentprocesses"
    response = get(url, headers=headers)
    if not response:
        raise OctopusApiError
    json = response.json()

    packages = []

    for step in json["Steps"]:
        name = step["Name"]
        for action in step["Actions"]:
            for package in action["Packages"]:
                packages.append({'DeploymentAction': name, 'PackageReference': package["Name"]})

    return packages


def create_channel(space_id, project_id, lifecycle_id, step_name, package_name, branch_name):
    if is_blank(space_id) or is_blank(project_id) or is_blank(lifecycle_id) or is_blank(branch_name):
        return None

    channel_id = find_channel(space_id, project_id, branch_name)

    if channel_id is not None:
        sys.stderr.write("Found channel " + channel_id + "\n")
        return channel_id

    packages = find_packages(space_id, project_id) if step_name is None or len(step_name.strip()) == 0 else \
        [{'DeploymentAction': step_name, 'PackageReference': package_name}]

    rules = list(map(lambda x: {
        'Tag': '^' + branch_name + '.*$',
        'Actions': [x["DeploymentAction"]],
        'ActionPackages': [x]}, packages))

    # Create the channel json
    channel = {
        'ProjectId': project_id,
        'Name': branch_name,
        'SpaceId': space_id,
        'IsDefault': False,
        'LifecycleId': lifecycle_id,
        'Rules': rules
    }

    url = args.octopus_url + "/api/" + space_id + "/projects/" + project_id + "/channels"
    response = post(url, headers=headers, json=channel)
    if not response:
        raise OctopusApiError
    json = response.json()
    sys.stderr.write("Created channel " + json["Id"] + "\n")
    return json["Id"]


def assign_target_by_name(space_id, environment_id, target_name):
    if is_blank(space_id) or is_blank(environment_id) or is_blank(target_name):
        return

    target_id = get_resource_id(space_id, "machines", target_name)
    if target_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/machines/" + target_id
        get_response = get(url, headers=headers)

        if not get_response:
            raise OctopusApiError

        target = get_response.json()

        if environment_id not in target["EnvironmentIds"]:
            target["EnvironmentIds"].append(environment_id)
            put_response = put(url, headers=headers, json=target)

            if not put_response:
                raise OctopusApiError

            sys.stderr.write("Added environment " + environment_id + " to target " + target_id + "\n")
        else:
            sys.stderr.write("Environment " + environment_id + " already assigned to target " + target_id + "\n")


def assign_target_by_role(space_id, environment_id, role_name):
    if is_blank(space_id) or is_blank(environment_id) or is_blank(role_name):
        return

    targets = find_targets_by_role(space_id, role_name)
    for target in targets:
        if environment_id not in target["EnvironmentIds"]:
            target["EnvironmentIds"].append(environment_id)
            url = args.octopus_url + "/api/" + space_id + "/machines/" + target["Id"]
            put_response = put(url, headers=headers, json=target)

            if not put_response:
                raise OctopusApiError

            sys.stderr.write("Added environment " + environment_id + " to target " + target["Id"] + "\n")
        else:
            sys.stderr.write("Environment " + environment_id + " already assigned to target " + target["Id"] + "\n")


def assign_target_by_role_and_environment(space_id, environment_id, role_name, existing_environment_name):
    if is_blank(space_id) or is_blank(environment_id) or is_blank(role_name) or is_blank(existing_environment_name):
        return

    existing_environment_id = get_resource_id(space_id, "environments", existing_environment_name)

    targets = find_targets_by_role(space_id, role_name)
    for target in targets:
        if existing_environment_id in target["EnvironmentIds"]:
            if environment_id not in target["EnvironmentIds"]:
                target["EnvironmentIds"].append(environment_id)
                url = args.octopus_url + "/api/" + space_id + "/machines/" + target["Id"]
                put_response = put(url, headers=headers, json=target)

                if not put_response:
                    raise OctopusApiError

                sys.stderr.write("Added environment " + environment_id + " to target " + target["Id"] + "\n")
            else:
                sys.stderr.write("Environment " + environment_id + " already assigned to target " + target["Id"] + "\n")


def cancel_tasks(space_id, project_id, branch_name):
    if is_blank(space_id) or is_blank(project_id) or is_blank(branch_name):
        return None

    number_active_tasks = 0
    channel_id = find_channel(space_id, project_id, branch_name)
    if channel_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/deployments?projects=" + project_id + "&channels=" + channel_id
        releases = get(url, headers=headers)
        json = releases.json()
        sys.stderr.write("Found " + str(len(json["Items"])) + " deployments\n")

        for deployment in json["Items"]:
            task_id = deployment["TaskId"]
            task_url = args.octopus_url + "/api/" + space_id + "/tasks/" + task_id
            task_response = get(task_url, headers=headers)
            task_json = task_response.json()

            if not task_json["IsCompleted"]:
                sys.stderr.write("Task " + task_id + " has not completed and will be cancelled\n")
                number_active_tasks += 1
                cancel_url = args.octopus_url + "/api/" + space_id + "/tasks/" + task_id + "/cancel"
                response = post(cancel_url, headers=headers)
                if not response:
                    raise OctopusApiError

    return number_active_tasks


def delete_releases(space_id, project_id, branch_name):
    if is_blank(space_id) or is_blank(project_id) or is_blank(branch_name):
        return

    channel_id = find_channel(space_id, project_id, branch_name)
    if channel_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/projects/" + project_id + "/releases"
        releases = get(url, headers=headers)
        json = releases.json()
        channel_releases = [a for a in json["Items"] if a["ChannelId"] == channel_id]
        for release in channel_releases:
            url = args.octopus_url + "/api/" + space_id + "/releases/" + release["Id"]
            response = delete(url, headers=headers)
            if not response:
                raise OctopusApiError


def delete_channel(space_id, project_id, branch_name):
    if is_blank(space_id) or is_blank(project_id) or is_blank(branch_name):
        return

    channel_id = find_channel(space_id, project_id, branch_name)
    if channel_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/projects/" + project_id + "/channels/" + channel_id
        response = delete(url, headers=headers)
        if not response:
            raise OctopusApiError
        sys.stderr.write("Deleted channel " + channel_id + "\n")


def delete_lifecycle(space_id, branch_name):
    if is_blank(space_id) or is_blank(branch_name):
        return

    lifecycle_id = get_resource_id(space_id, "lifecycles", branch_name)

    if lifecycle_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/lifecycles/" + lifecycle_id
        response = delete(url, headers=headers)
        if not response:
            raise OctopusApiError
        sys.stderr.write("Deleted lifecycle " + lifecycle_id + "\n")


def delete_environment(space_id, branch_name):
    if is_blank(space_id) or is_blank(branch_name):
        return

    environment_id = get_resource_id(space_id, "environments", branch_name)

    if environment_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/environments/" + environment_id
        response = delete(url, headers=headers)
        if not response:
            raise OctopusApiError
        sys.stderr.write("Deleted environment " + environment_id + "\n")


def delete_target(space_id, target_id):
    if is_blank(space_id) or is_blank(target_id):
        return

    url = args.octopus_url + "/api/" + space_id + "/machines/" + target_id
    response = delete(url, headers=headers)

    if not response:
        raise OctopusApiError


def unassign_target_by_name(space_id, branch_name, target_name):
    if is_blank(space_id) or is_blank(branch_name) or is_blank(target_name):
        return

    environment_id = get_resource_id(space_id, "environments", branch_name)

    if environment_id is None:
        return

    target_id = get_resource_id(space_id, "machines", target_name)
    if target_id is not None:
        url = args.octopus_url + "/api/" + space_id + "/machines/" + target_id
        get_response = get(url, headers=headers)

        if not get_response:
            raise OctopusApiError

        target = get_response.json()

        if environment_id not in target["EnvironmentIds"]:
            target["EnvironmentIds"] = [a for a in target["EnvironmentIds"] if a != environment_id]
            if len(target["EnvironmentIds"]) == 0:
                delete_target(target["Id"])
                sys.stderr.write("Removed target " + target["Id"] + " because it was only assigned to the environment "
                                 + environment_id)
            else:
                put_response = put(url, headers=headers, json=target)

                if not put_response:
                    raise OctopusApiError

                sys.stderr.write("Removed environment " + environment_id + " from target " + target_id + "\n")
        else:
            sys.stderr.write("Environment " + environment_id + " not assigned to target " + target_id + "\n")


def unassign_target(space_id, branch_name):
    if is_blank(space_id) or is_blank(branch_name):
        return

    environment_id = get_resource_id(space_id, "environments", branch_name)

    if environment_id is None:
        return

    targets = find_targets(space_id)
    for target in targets:
        if environment_id in target["EnvironmentIds"]:
            target["EnvironmentIds"] = [a for a in target["EnvironmentIds"] if a != environment_id]

            if len(target["EnvironmentIds"]) == 0:
                delete_target(target["Id"])
                sys.stderr.write("Removed target " + target["Id"] + " because it was only assigned to the environment "
                                 + environment_id)
            else:
                url = args.octopus_url + "/api/" + space_id + "/machines/" + target["Id"]
                put_response = put(url, headers=headers, json=target)

                if not put_response:
                    raise OctopusApiError

                sys.stderr.write("Removed environment " + environment_id + " to target " + target["Id"] + "\n")
        else:
            sys.stderr.write("Environment " + environment_id + " not assigned to target " + target["Id"] + "\n")


@retry_on_communication_error
def create_feature_branch():
    space_id = get_space_id(args.octopus_space)
    project_id = get_resource_id(space_id, "projects", args.octopus_project)
    environment_id = create_environment(space_id, args.branch_name)
    lifecycle_id = create_lifecycle(space_id, environment_id, args.branch_name)
    create_channel(space_id, project_id, lifecycle_id, args.deployment_step_name, args.deployment_package_name,
                   args.branch_name)
    if is_blank(args.target_name):
        if is_blank(args.target_environment):
            assign_target_by_role(space_id, environment_id, args.target_role)
        else:
            assign_target_by_role_and_environment(space_id, environment_id, args.target_role, args.target_environment)
    else:
        assign_target_by_name(space_id, environment_id, args.target_name)


@retry_on_communication_error
def delete_feature_branch():
    space_id = get_space_id(args.octopus_space)
    project_id = get_resource_id(space_id, "projects", args.octopus_project)

    while True:
        tasks = cancel_tasks(space_id, project_id, args.branch_name)
        if tasks == 0:
            break
        time.sleep(10)

    delete_releases(space_id, project_id, args.branch_name)
    delete_channel(space_id, project_id, args.branch_name)
    delete_lifecycle(space_id, args.branch_name)
    unassign_target(space_id, args.branch_name)
    delete_environment(space_id, args.branch_name)


def main():
    if args.branch_name in IGNORED_BRANCHES:
        return

    if args.action == 'create':
        create_feature_branch()

    if args.action == 'delete':
        delete_feature_branch()


args = parse_args()
headers = build_headers()
main()
