import json
import os
import time
import paramiko

HOST = "10.194.78.12"
PORT = 22
USERNAME = "pgat.dnew"
PASSWORD = None # Recommended: leave as None so PyCharm prompts you securely.

WATCH = True
INTERVAL_SECONDS = 30

REMOTE_CMD = (
    "printf '%s\\n' '__DOCKER_INFO__'; "
    "docker info --format '{{json .}}'; "
    "printf '%s\\n' '__DOCKER_PS__'; "
    "docker ps -a --format '{{json .}}'"
)


def get_ssh_password():
    if PASSWORD:
        return PASSWORD

    env_password = os.getenv("SSH_PASSWORD")
    if env_password:
        return env_password

    raise ValueError(
        "No SSH password configured. Set PASSWORD in this file or add SSH_PASSWORD "
        "to the PyCharm run configuration environment variables."
    )


def run_remote_command(command):
    print(f"Connecting to {USERNAME}@{HOST}:{PORT}...")

    password = get_ssh_password()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=HOST,
            port=PORT,
            username=USERNAME,
            password=password,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )

        print("Connected. Running Docker status command...")

        stdin, stdout, stderr = client.exec_command(command, timeout=35)

        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        print(f"Remote command finished with exit code {exit_code}")

        return exit_code, output, error

    except paramiko.AuthenticationException as exc:
        raise RuntimeError(
            "SSH authentication failed. Check USERNAME/PASSWORD and confirm the "
            "server allows password login for this account."
        ) from exc
    except paramiko.SSHException as exc:
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"SSH connection timed out while connecting to {HOST}:{PORT}.") from exc
    finally:
        client.close()
        print("SSH connection closed.")


def parse_output(stdout):
    section = None
    info = {}
    containers = []

    for line in stdout.splitlines():
        if line == "__DOCKER_INFO__":
            section = "info"
            continue

        if line == "__DOCKER_PS__":
            section = "containers"
            continue

        if not line.strip():
            continue

        if section == "info":
            info = json.loads(line)
        elif section == "containers":
            containers.append(json.loads(line))

    return info, containers


def container_state(container):
    state = container.get("State", "").lower()
    status = container.get("Status", "").lower()

    if "unhealthy" in status:
        return "UNHEALTHY"
    if state == "running":
        return "RUNNING"
    if state:
        return state.upper()

    return "UNKNOWN"


def check_docker_status():
    print("Starting Docker status check...")

    try:
        exit_code, output, error = run_remote_command(REMOTE_CMD)
    except Exception as exc:
        print(f"\nConnection failed: {exc}")
        print("-" * 60)
        return

    if error.strip():
        print("\nRemote stderr:")
        print(error.strip())

    if exit_code != 0:
        print("\nDocker status: UNAVAILABLE")
        return

    info, containers = parse_output(output)

    print("\nDocker status: RUNNING")
    print(f"Host: {info.get('Name', 'unknown')}")
    print(f"Version: {info.get('ServerVersion', 'unknown')}")
    print(f"Images: {info.get('Images', 'unknown')}")
    print(
        "Containers: "
        f"total={info.get('Containers', len(containers))}, "
        f"running={info.get('ContainersRunning', 'unknown')}, "
        f"paused={info.get('ContainersPaused', 'unknown')}, "
        f"stopped={info.get('ContainersStopped', 'unknown')}"
    )

    print("\nContainers:")
    for container in containers:
        state = container_state(container)
        print(
            f"{state:10} "
            f"{container.get('Names', '<unknown>')} | "
            f"{container.get('Image', '<unknown>')} | "
            f"{container.get('Status', '')}"
        )

    print("-" * 60)


def main():
    if WATCH:
        while True:
            check_docker_status()
            time.sleep(INTERVAL_SECONDS)
    else:
        check_docker_status()


if __name__ == "__main__":
    main()
