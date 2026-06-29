import csv
import json
import getpass
import logging
import os
import shlex
import smtplib
import ssl
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import paramiko

logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

try:
    import tkinter as tk
    from tkinter import ttk, font as tkfont
except ImportError:
    tk = None
    ttk = None
    tkfont = None

HOST_OPTIONS = (
    ("10.194.78.11", "prometheus.ds.maxar.com"),
    ("10.194.78.13", "cerebro.ds.maxar.com"),
    ("10.194.78.12", "magneto.ds.maxar.com"),
    ("10.194.78.10", "atlas.ds.maxar.com"),
)
HOST_CHOICES = [f"{ip} - {name}" for ip, name in HOST_OPTIONS]
HOST_BY_CHOICE = dict(zip(HOST_CHOICES, [ip for ip, _name in HOST_OPTIONS]))
HOST = HOST_OPTIONS[0][0]
PORT = 22
USERNAME = "pgat.dnew"
PASSWORD = None  # Leave as None to prompt securely.
CACHED_PASSWORD = None
DASHBOARD_LINKS = (
    ("USARPAC", "https://prometheus.ds.maxar.com:3001"),
    ("Ft Gordon", "https://magneto.ds.maxar.com:3001"),
)
DASHBOARD_BUTTON_BG = "#2e7d32"
DASHBOARD_BUTTON_ACTIVE_BG = "#1b5e20"
DB_SSH_HOST = "10.194.78.12"
DB_SSH_PORT = 22
DB_SSH_USERNAME = "pgat.dnew"
DB_NAME = "dcs"
DB_USERNAME = "reveal"
DB_SSH_PASSWORD_ENV = "PE_MONITOR_DB_SSH_PASSWORD"
DB_PSQL_COMMAND_ENV = "PE_MONITOR_PSQL_COMMAND"
DB_PSQL_CONTAINER_ENV = "PE_MONITOR_PSQL_CONTAINER"
DB_DEFAULT_PSQL_CONTAINER = "deepcore-postgres-1"
DB_JOBS_QUERY = """
select
  state,
  jobrequest::jsonb ->> 'model' as model,
  jobrequest::jsonb #>> '{image,url}' as image,
  timeelapsed as timeselapsed
from jobs
where state in ('Running', 'Queued')
  and jobrequest like '{%'
order by
  case
    when state = 'Running' then 1
    when state = 'Queued' then 2
  end
"""

"""
Added the Alerts tab in monitor_v1.py. It asks for first name, last name, and email, includes a Send Test Email button,
and sends an alert when a container is first observed down or transitions from healthy to down/missing. It will not keep
sending every refresh while the same container remains down.
Email sending uses SMTP via environment variables in monitor_v1.py. Set these before launching the app:
$env:PE_MONITOR_SMTP_HOST="smtp.yourdomain.com"
$env:PE_MONITOR_SMTP_PORT="587"
$env:PE_MONITOR_SMTP_USERNAME="your-user"
$env:PE_MONITOR_SMTP_PASSWORD="your-password"
$env:PE_MONITOR_SMTP_FROM="pe-monitor@yourdomain.com"
Optional: set PE_MONITOR_SMTP_USE_TLS=false for a non-TLS relay on port 25, or PE_MONITOR_SMTP_USE_SSL=true for SSL on
port 465.
Verification: syntax check passed, diff whitespace check passed, and a stubbed import check passed. A normal import in
this shell still fails because paramiko is not installed in the current Python interpreter; that was an existing
dependency for the app.
"""

SMTP_HOST_ENV = "PE_MONITOR_SMTP_HOST"
SMTP_PORT_ENV = "PE_MONITOR_SMTP_PORT"
SMTP_USERNAME_ENV = "PE_MONITOR_SMTP_USERNAME"
SMTP_PASSWORD_ENV = "PE_MONITOR_SMTP_PASSWORD"
SMTP_FROM_ENV = "PE_MONITOR_SMTP_FROM"
SMTP_USE_TLS_ENV = "PE_MONITOR_SMTP_USE_TLS"
SMTP_USE_SSL_ENV = "PE_MONITOR_SMTP_USE_SSL"

WATCH = True
INTERVAL_SECONDS = 30
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
CLOCK_TIMEZONES = (
    "0  Zulu      - Greenwich, England",
    "1  Alpha     - Paris, France",
    "2  Bravo     - Athens, Greece",
    "3  Charlie   - Moscow, Russia",
    "4  Delta     - Kabul, Afghanistan",
    "5  Echo      - New Delhi, India",
    "6  Foxtrot   - Dhanka, Bangladesh",
    "7  Golf      - Bangkok, Thailand",
    "8  Hotel     - Beijing, China",
    "9  India     - Tokyo, Japan",
    "10 Kilo      - Sydney, Australia",
    "11 Lima      - Honiara, Solomon Islands",
    "12 Mike      - Wellington, New Zealand",
    "-1 November  - Azores, Portugal"
    "-2 Oscar     - Godthab, Greenland",
    "-3 Papa      - Buenos Aires, Argentina",
    "-4 Quebec    - Halifax, Nova Scotia",
    "-5 Romeo     - Washington, USA",
    "-6 Sierra    - Dallas, TX (United States)",
    "-7 Tango     - Denver, CO (United States)",
    "-8 Uniform   - Seattle, WA (United States)",
    "-9 Victor    - Juneau, AK (United States)",
    "-10 Whiskey  - Honolulu, HI (United States)",
    "-11 X-Ray    - Nome, AK (United States)",
    "-12 Yankee   - Suva, Fiji",
)
DEFAULT_CLOCK_TIMEZONES = (
    "0  Zulu      - Greenwich, England",
    "-5 Romeo     - Washington, USA",
    "-10 Whiskey  - Honolulu, HI (United States)",
    "10 Kilo      - Sydney, Australia",

)
CLOCK_FALLBACK_OFFSETS = {
    "0  Zulu      - Greenwich, England": 0,
    "1  Alpha     - Paris, France": 1,
    "2  Bravo     - Athens, Greece": 2,
    "3  Charlie   - Moscow, Russia": 3,
    "4  Delta     - Kabul, Afghanistan": 4,
    "5  Echo      - New Delhi, India": 5,
    "6  Foxtrot   - Dhanka, Bangladesh": 6,
    "7  Golf      - Bangkok, Thailand": 7,
    "8  Hotel     - Beijing, China": 8,
    "9  India     - Tokyo, Japan": 9,
    "10 Kilo      - Sydney, Australia": 10,
    "11 Lima      - Honiara, Solomon Islands": 11,
    "12 Mike      - Wellington, New Zealand":12 ,
    "-1 November  - Azores, Portugal": -1,
    "-2 Oscar     - Godthab, Greenland": -2,
    "-3 Papa      - Buenos Aires, Argentina": -3,
    "-4 Quebec    - Halifax, Nova Scotia": -4,
    "-5 Romeo     - Washington, USA": -5,
    "-6 Sierra    - Dallas, TX (United States)": -6,
    "-7 Tango     - Denver, CO (United States)": -7,
    "-8 Uniform   - Seattle, WA (United States)": -8,
    "-9 Victor    - Juneau, AK (United States)": -9,
    "-10 Whiskey  - Honolulu, HI (United States)": -10,
    "-11 X-Ray    - Nome, AK (United States)": -11,
    "-12 Yankee   - Suva, Fiji": -12,
}

REMOTE_CMD = (
    "docker_exit=0; "
    "printf '%s\\n' '__DOCKER_INFO__'; "
    "docker info --format '{{json .}}' || docker_exit=$?; "
    "printf '%s\\n' '__DOCKER_PS__'; "
    "docker ps -a --format '{{json .}}' || docker_exit=$?; "
    "printf '%s\\n' '__GPU_INFO__'; "
    "if command -v nvidia-smi >/dev/null 2>&1; then "
    "nvidia-smi "
    "--query-gpu=index,name,uuid,temperature.gpu,utilization.gpu,memory.used,"
    "memory.total,power.draw,power.limit "
    "--format=csv,noheader,nounits 2>/dev/null || printf '%s\\n' '__GPU_UNAVAILABLE__'; "
    "else printf '%s\\n' '__GPU_UNAVAILABLE__'; fi; "
    "exit $docker_exit"
)


def green_text(text):
    return f"{GREEN}{text}{RESET}"


def red_text(text):
    return f"{RED}{text}{RESET}"


def read_masked_password(prompt):
    print(prompt, end="", flush=True)

    if os.name == "nt":
        try:
            import msvcrt

            password_chars = []

            while True:
                char = msvcrt.getwch()

                if char in ("\r", "\n"):
                    print()
                    return "".join(password_chars)

                if char == "\003":
                    raise KeyboardInterrupt

                if char == "\b":
                    if password_chars:
                        password_chars.pop()
                        print("\b \b", end="", flush=True)
                    continue

                password_chars.append(char)
                print("*", end="", flush=True)
        except OSError:
            pass

    try:
        import termios
        import tty
    except ImportError:
        return getpass.getpass("", stream=sys.stdout)

    password_chars = []
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)

        while True:
            char = sys.stdin.read(1)

            if char in ("\r", "\n"):
                print()
                return "".join(password_chars)

            if char == "\003":
                raise KeyboardInterrupt

            if char in ("\b", "\x7f"):
                if password_chars:
                    password_chars.pop()
                    print("\b \b", end="", flush=True)
                continue

            password_chars.append(char)
            print("*", end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def get_ssh_password(username=USERNAME, host=HOST):
    global CACHED_PASSWORD

    if PASSWORD:
        return PASSWORD

    if CACHED_PASSWORD:
        return CACHED_PASSWORD

    env_password = os.getenv("SSH_PASSWORD")
    if env_password:
        return env_password

    prompt = f"Password for {username}@{host}: "

    CACHED_PASSWORD = read_masked_password(prompt)
    return CACHED_PASSWORD


def clear_cached_password():
    global CACHED_PASSWORD
    CACHED_PASSWORD = None


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in ("1", "true", "yes", "on")


def valid_email_address(email):
    parsed = parseaddr(email)[1]
    if parsed != email.strip():
        return False

    local, separator, domain = parsed.partition("@")
    return bool(local and separator and "." in domain)


def smtp_port(use_tls, use_ssl):
    configured_port = os.getenv(SMTP_PORT_ENV)
    if configured_port:
        return int(configured_port)

    if use_ssl:
        return 465

    if use_tls:
        return 587

    return 25


def send_email(to_address, subject, body, recipient_name=None):
    smtp_host = os.getenv(SMTP_HOST_ENV)
    if not smtp_host:
        raise RuntimeError(f"Set {SMTP_HOST_ENV} before sending alert email.")

    configured_port = os.getenv(SMTP_PORT_ENV)
    use_ssl = env_bool(SMTP_USE_SSL_ENV, default=configured_port == "465")
    use_tls = env_bool(
        SMTP_USE_TLS_ENV,
        default=not use_ssl and configured_port != "25",
    )
    username = os.getenv(SMTP_USERNAME_ENV)
    password = os.getenv(SMTP_PASSWORD_ENV)
    from_address = os.getenv(SMTP_FROM_ENV) or username or "pe-monitor@localhost"
    port = smtp_port(use_tls, use_ssl)

    message = EmailMessage()
    message["From"] = formataddr(("PE Monitor", from_address))
    message["To"] = formataddr((recipient_name or "", to_address))
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, port, timeout=15, context=context) as server:
            if username:
                server.login(username, password or "")
            server.send_message(message)
        return

    with smtplib.SMTP(smtp_host, port, timeout=15) as server:
        server.ehlo()
        if use_tls:
            server.starttls(context=context)
            server.ehlo()
        if username:
            server.login(username, password or "")
        server.send_message(message)


def db_psql_command_prefix():
    override = os.getenv(DB_PSQL_COMMAND_ENV)
    if override:
        return override

    container = os.getenv(DB_PSQL_CONTAINER_ENV) or DB_DEFAULT_PSQL_CONTAINER
    if container:
        return (
            f"docker exec -i {shlex.quote(container)} "
            "bash -lc 'exec psql \"$@\"' pe-monitor-psql"
        )

    return (
        "run_pe_monitor_psql() { "
        "PSQL=$(command -v psql 2>/dev/null || true); "
        "if [ -n \"$PSQL\" ]; then \"$PSQL\" \"$@\"; return $?; fi; "
        "if command -v docker >/dev/null 2>&1; then "
        "for candidate in /usr/bin/psql /usr/local/bin/psql /bin/psql /usr/pgsql-*/bin/psql; do "
        "if [ -x \"$candidate\" ]; then \"$candidate\" \"$@\"; return $?; fi; "
        "done; "
        "for container in $(docker ps --format '{{.Names}}'); do "
        "if docker exec \"$container\" bash -lc 'command -v psql >/dev/null 2>&1'; then "
        "docker exec -i \"$container\" bash -lc 'exec psql \"$@\"' pe-monitor-psql \"$@\"; return $?; "
        "fi; "
        "done; "
        "fi; "
        f"echo \"psql not found. Install the PostgreSQL client, set {DB_PSQL_COMMAND_ENV}, or set {DB_PSQL_CONTAINER_ENV}.\" >&2; "
        "exit 127; "
        "}; "
        "run_pe_monitor_psql"
    )


def db_jobs_command():
    wrapped_query = (
        "select coalesce(json_agg(row_to_json(job_rows)), '[]'::json) "
        f"from ({DB_JOBS_QUERY}) job_rows"
    )
    return (
        f"{db_psql_command_prefix()} -U {shlex.quote(DB_USERNAME)} "
        f"-d {shlex.quote(DB_NAME)} -X -q -t -A "
        f"--set ON_ERROR_STOP=1 -c {shlex.quote(wrapped_query)}"
    )


def parse_db_jobs_output(output):
    output = output.strip()
    if not output:
        return []

    rows = json.loads(output)
    if not isinstance(rows, list):
        raise RuntimeError("Database query did not return a JSON row list.")

    return rows


def path_basename(value):
    value = str(value or "").rstrip("/")
    if not value:
        return ""

    return value.rsplit("/", 1)[-1]


def seconds_to_hhmmss(seconds):
    seconds = max(0, int(round(float(seconds))))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_elapsed(value):
    if value in (None, ""):
        return ""

    if isinstance(value, (int, float)):
        return seconds_to_hhmmss(float(value) / 1000)

    text = str(value).strip()
    if not text:
        return ""

    try:
        return seconds_to_hhmmss(float(text) / 1000)
    except ValueError:
        pass

    days = 0
    tokens = text.replace(",", " ").split()
    for index, token in enumerate(tokens[:-1]):
        if tokens[index + 1].lower().startswith("day"):
            try:
                days = int(float(token))
            except ValueError:
                days = 0
            break

    time_token = next((token for token in tokens if ":" in token), text)
    time_parts = time_token.split(".", 1)[0].split(":")
    if len(time_parts) == 3:
        try:
            hours, minutes, seconds = [int(float(part)) for part in time_parts]
        except ValueError:
            return text

        return seconds_to_hhmmss((days * 24 * 3600) + (hours * 3600) + (minutes * 60) + seconds)

    return text


def remote_command_failure_message(result):
    message = result.get("exception") or f"Remote command exited with {result['exit_code']}."
    command = result.get("command", "").strip()
    error = result.get("error", "").strip()
    if command:
        message = f"{message} Remote command: {command}"

    if error:
        return f"{message} Remote stderr: {error}"

    return message


def run_remote_command(command, username=None, password=None, host=None, port=None):
    host = host or HOST
    port = port or PORT
    username = username or USERNAME
    password = password or get_ssh_password(username, host)

    print(f"Connecting to {username}@{host}:{port}...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )

        print("Connected. Running remote command...")

        stdin, stdout, stderr = client.exec_command(command, timeout=35)

        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        print(f"Remote command finished with exit code {exit_code}")
        if exit_code != 0:
            print("Remote command that failed:")
            print(command)

        return exit_code, output, error

    except paramiko.AuthenticationException as exc:
        clear_cached_password()
        raise RuntimeError(
            "SSH authentication failed. Check USERNAME/PASSWORD and confirm the "
            "server allows password login for this account."
        ) from exc
    except paramiko.SSHException as exc:
        message = str(exc)
        if "Error reading SSH protocol banner" in message:
            raise RuntimeError(
                f"SSH server at {host}:{port} closed the connection before sending "
                "an SSH banner. Confirm this host and port accept SSH from your "
                f"machine by running: ssh -vvv -p {port} {username}@{host}"
            ) from exc

        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"SSH socket error while connecting to {host}:{port}: {exc}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"SSH connection timed out while connecting to {host}:{port}.") from exc
    finally:
        client.close()
        print("SSH connection closed.")


def parse_output(stdout):
    section = None
    info = {}
    containers = []
    gpus = []
    gpu_available = False

    for line in stdout.splitlines():
        if line == "__DOCKER_INFO__":
            section = "info"
            continue

        if line == "__DOCKER_PS__":
            section = "containers"
            continue

        if line == "__GPU_INFO__":
            section = "gpus"
            gpu_available = True
            continue

        if line == "__GPU_UNAVAILABLE__":
            gpu_available = False
            continue

        if not line.strip():
            continue

        if section == "info":
            info = json.loads(line)
        elif section == "containers":
            containers.append(json.loads(line))
        elif section == "gpus":
            gpu = parse_gpu_line(line)
            if gpu:
                gpus.append(gpu)

    if gpu_available and not gpus:
        gpu_available = False

    return info, containers, gpus, gpu_available


def parse_gpu_line(line):
    values = next(csv.reader([line]), [])

    if len(values) != 9:
        return None

    values = [value.strip() for value in values]

    return {
        "index": values[0],
        "name": values[1],
        "uuid": values[2],
        "temperature": values[3],
        "utilization": values[4],
        "memory_used": values[5],
        "memory_total": values[6],
        "power_draw": values[7],
        "power_limit": values[8],
    }


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


def container_is_healthy(container):
    return container_state(container) == "RUNNING"


def docker_health(containers):
    if all(container_is_healthy(container) for container in containers):
        return "HEALTHY"

    return "UNHEALTHY"


def metric_number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def metric_ratio(value, maximum):
    value_number = metric_number(value)
    maximum_number = metric_number(maximum)

    if value_number is None or maximum_number is None or maximum_number <= 0:
        return None

    return max(0, min(1, value_number / maximum_number))


def format_metric(value, unit):
    value_number = metric_number(value)

    if value_number is None:
        return "N/A"

    if value_number.is_integer():
        return f"{int(value_number)} {unit}"

    return f"{value_number:.1f} {unit}"


def gpu_is_healthy(gpu):
    temperature = metric_number(gpu.get("temperature"))
    return temperature is None or temperature < 85


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

    info, containers, gpus, gpu_available = parse_output(output)
    docker_status = docker_health(containers)
    docker_status_line = f"Docker status: {docker_status}"

    if docker_status == "HEALTHY":
        print(f"\n{green_text(docker_status_line)}")
    else:
        print(f"\n{red_text(docker_status_line)}")
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

    print("\nGPUs:")
    if gpu_available:
        for gpu in gpus:
            print(
                f"GPU {gpu.get('index', '?')}: "
                f"{gpu.get('name', 'unknown')} | "
                f"temp={format_metric(gpu.get('temperature'), 'C')} | "
                f"util={format_metric(gpu.get('utilization'), '%')} | "
                "memory="
                f"{format_metric(gpu.get('memory_used'), 'MiB')}/"
                f"{format_metric(gpu.get('memory_total'), 'MiB')} | "
                "power="
                f"{format_metric(gpu.get('power_draw'), 'W')}/"
                f"{format_metric(gpu.get('power_limit'), 'W')}"
            )
    else:
        print("GPU status: UNAVAILABLE")

    print("-" * 60)


class DockerMonitorApp:
    def __init__(self, root):
        self.root = root
        self.check_running = False
        self.db_query_running = False
        self.next_check_id = None
        self.container_resize_id = None
        self.gpu_resize_id = None
        self.last_containers = None
        self.last_gpus = None
        self.last_gpu_available = False
        self.container_alert_snapshot = None

        self.selected_host_var = tk.StringVar(value=HOST_CHOICES[0])
        self.username_var = tk.StringVar(value=USERNAME)
        self.password_var = tk.StringVar()
        self.connection_status_var = tk.StringVar(
            value="Enter username and password, then click Check Now."
        )
        self.docker_status_var = tk.StringVar(value="Docker status: UNKNOWN")
        self.host_var = tk.StringVar(value="Host: unknown")
        self.version_var = tk.StringVar(value="Version: unknown")
        self.images_var = tk.StringVar(value="Images: unknown")
        self.container_count_var = tk.StringVar(value="Containers: unknown")
        self.gpu_status_var = tk.StringVar(value="GPU status: UNKNOWN")
        self.gpu_count_var = tk.StringVar(value="GPUs: unknown")
        self.refresh_var = tk.StringVar(value=f"Auto refresh: {INTERVAL_SECONDS}s")
        self.alert_first_name_var = tk.StringVar()
        self.alert_last_name_var = tk.StringVar()
        self.alert_email_var = tk.StringVar()
        self.alert_status_var = tk.StringVar(value="Enter alert contact details.")
        self.db_password_var = tk.StringVar()
        self.db_status_var = tk.StringVar(
            value="Enter the reveal SSH password, then click Refresh."
        )
        self.clock_timezone_vars = [
            tk.StringVar(value=timezone_name)
            for timezone_name in DEFAULT_CLOCK_TIMEZONES
        ]
        self.clock_date_vars = [tk.StringVar(value="-- --- ----") for _index in range(4)]
        self.clock_time_vars = [tk.StringVar(value="----") for _index in range(4)]

        self.root.title("PE Monitor")
        self.root.geometry("1058x741")
        self.root.minsize(874, 582)

        self.build_ui()
        self.update_clocks()
        self.password_entry.focus_set()

    def build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.connection_tab = ttk.Frame(self.notebook, padding=18)
        self.containers_tab = ttk.Frame(self.notebook, padding=18)
        self.gpus_tab = ttk.Frame(self.notebook, padding=18)
        self.alerts_tab = ttk.Frame(self.notebook, padding=18)
        self.db_tab = ttk.Frame(self.notebook, padding=18)

        self.notebook.add(self.connection_tab, text="Connection")
        self.notebook.add(self.containers_tab, text="Containers")
        self.notebook.add(self.gpus_tab, text="GPUs")
        self.notebook.add(self.alerts_tab, text="Alerts")
        self.notebook.add(self.db_tab, text="DCS Jobs")

        self.build_connection_tab()
        self.build_containers_tab()
        self.build_gpus_tab()
        self.build_alerts_tab()
        self.build_db_tab()

    def build_clock_bar(self, parent, layout="pack"):
        clock_bar = ttk.Frame(parent)

        if layout == "grid":
            clock_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
            parent.columnconfigure(0, weight=1)
        else:
            clock_bar.pack(fill="x", pady=(0, 12))

        for index in range(4):
            clock_bar.columnconfigure(index, weight=1)

            clock_frame = ttk.Frame(clock_bar)
            clock_frame.grid(row=0, column=index, sticky="ew", padx=4)

            ttk.Combobox(
                clock_frame,
                textvariable=self.clock_timezone_vars[index],
                values=CLOCK_TIMEZONES,
                state="readonly",
            ).pack(fill="x")

            tk.Label(
                clock_frame,
                textvariable=self.clock_date_vars[index],
                anchor="center",
                font=("Segoe UI", 12, "bold"),
                fg="black",
            ).pack(fill="x", pady=(4, 0))

            tk.Label(
                clock_frame,
                textvariable=self.clock_time_vars[index],
                anchor="center",
                font=("Segoe UI", 18, "bold"),
                fg="#ff8c00",
            ).pack(fill="x")

        return clock_bar

    def update_clocks(self):
        for index, timezone_var in enumerate(self.clock_timezone_vars):
            clock_date, clock_time = self.clock_parts(timezone_var.get())
            self.clock_date_vars[index].set(clock_date)
            self.clock_time_vars[index].set(clock_time)

        self.root.after(1000, self.update_clocks)

    def clock_parts(self, timezone_name):
        if timezone_name == "UTC" or ZoneInfo is None:
            now = datetime.now(self.clock_timezone_fallback(timezone_name))
        else:
            try:
                now = datetime.now(ZoneInfo(timezone_name))
            except ZoneInfoNotFoundError:
                now = datetime.now(self.clock_timezone_fallback(timezone_name))

        return now.strftime("%d %b %Y").upper(), now.strftime("%H%M")

    def clock_timezone_fallback(self, timezone_name):
        offset = CLOCK_FALLBACK_OFFSETS.get(timezone_name, 0)
        return timezone(timedelta(hours=offset))

    def build_connection_tab(self):
        self.build_clock_bar(self.connection_tab, layout="grid")

        form = ttk.Frame(self.connection_tab)
        form.grid(row=1, column=0, sticky="ew")
        self.connection_tab.columnconfigure(0, weight=1)
        self.connection_tab.rowconfigure(5, weight=1)

        ttk.Label(form, text="Host").grid(row=0, column=0, sticky="w", pady=5)
        self.host_combo = ttk.Combobox(
            form,
            textvariable=self.selected_host_var,
            values=HOST_CHOICES,
            state="readonly",
        )
        self.host_combo.grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Username").grid(row=1, column=0, sticky="w", pady=5)
        self.username_entry = ttk.Entry(form, textvariable=self.username_var)
        self.username_entry.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Password").grid(row=2, column=0, sticky="w", pady=5)
        self.password_entry = ttk.Entry(
            form,
            textvariable=self.password_var,
            show="*",
        )
        self.password_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.password_entry.bind("<Return>", lambda _event: self.check_now())

        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self.connection_tab)
        buttons.grid(row=2, column=0, sticky="w", pady=(18, 8))

        self.check_button = ttk.Button(buttons, text="Check Now", command=self.check_now)
        self.check_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(buttons, text="Clear Password", command=self.clear_password).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )

        ttk.Label(self.connection_tab, textvariable=self.refresh_var).grid(
            row=3,
            column=0,
            sticky="w",
            pady=(4, 12),
        )

        self.connection_status_label = tk.Label(
            self.connection_tab,
            textvariable=self.connection_status_var,
            anchor="w",
            fg="black",
        )
        self.connection_status_label.grid(row=4, column=0, sticky="ew")

        dashboard_buttons = ttk.Frame(self.connection_tab)
        dashboard_buttons.grid(row=6, column=0, sticky="sw", pady=(18, 0))

        for column, (label, url) in enumerate(DASHBOARD_LINKS):
            tk.Button(
                dashboard_buttons,
                text=label,
                command=lambda link_label=label, link_url=url: self.open_dashboard(
                    link_label,
                    link_url,
                ),
                bg=DASHBOARD_BUTTON_BG,
                fg="white",
                activebackground=DASHBOARD_BUTTON_ACTIVE_BG,
                activeforeground="white",
                padx=12,
                pady=4,
                cursor="hand2",
            ).grid(row=0, column=column, padx=(0, 8))

    def open_dashboard(self, label, url):
        webbrowser.open_new(url)
        self.set_connection_status(f"Opening {label}: {url}", "black")

    def build_containers_tab(self):
        self.build_clock_bar(self.containers_tab)

        summary = ttk.Frame(self.containers_tab)
        summary.pack(fill="x", pady=(0, 12))

        self.docker_status_label = tk.Label(
            summary,
            textvariable=self.docker_status_var,
            anchor="w",
            font=("Segoe UI", 11, "bold"),
            fg="black",
        )
        self.docker_status_label.grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Label(summary, textvariable=self.host_var).grid(row=1, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.version_var).grid(row=2, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.images_var).grid(row=3, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.container_count_var).grid(
            row=4,
            column=0,
            sticky="w",
        )

        checkerboard_frame = ttk.Frame(self.containers_tab)
        checkerboard_frame.pack(fill="both", expand=True)

        self.container_canvas = tk.Canvas(
            checkerboard_frame,
            bg="#f3f4f6",
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            checkerboard_frame,
            orient="vertical",
            command=self.container_canvas.yview,
        )
        self.container_canvas.configure(yscrollcommand=scrollbar.set)

        self.container_grid = tk.Frame(self.container_canvas, bg="#f3f4f6")
        self.container_grid_window = self.container_canvas.create_window(
            (0, 0),
            window=self.container_grid,
            anchor="nw",
        )
        self.container_grid.bind("<Configure>", self.update_checkerboard_scroll_region)
        self.container_canvas.bind("<Configure>", self.resize_checkerboard)
        self.container_canvas.bind("<MouseWheel>", self.scroll_checkerboard)

        self.container_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        checkerboard_frame.columnconfigure(0, weight=1)
        checkerboard_frame.rowconfigure(0, weight=1)

    def build_gpus_tab(self):
        self.build_clock_bar(self.gpus_tab)

        summary = ttk.Frame(self.gpus_tab)
        summary.pack(fill="x", pady=(0, 12))

        self.gpu_status_label = tk.Label(
            summary,
            textvariable=self.gpu_status_var,
            anchor="w",
            font=("Segoe UI", 11, "bold"),
            fg="black",
        )
        self.gpu_status_label.grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Label(summary, textvariable=self.gpu_count_var).grid(row=1, column=0, sticky="w")

        gpu_frame = ttk.Frame(self.gpus_tab)
        gpu_frame.pack(fill="both", expand=True)

        self.gpu_canvas = tk.Canvas(
            gpu_frame,
            bg="#f3f4f6",
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            gpu_frame,
            orient="vertical",
            command=self.gpu_canvas.yview,
        )
        self.gpu_canvas.configure(yscrollcommand=scrollbar.set)

        self.gpu_grid = tk.Frame(self.gpu_canvas, bg="#f3f4f6")
        self.gpu_grid_window = self.gpu_canvas.create_window(
            (0, 0),
            window=self.gpu_grid,
            anchor="nw",
        )
        self.gpu_grid.bind("<Configure>", self.update_gpu_scroll_region)
        self.gpu_canvas.bind("<Configure>", self.resize_gpu_grid)
        self.gpu_canvas.bind("<MouseWheel>", self.scroll_gpu_grid)

        self.gpu_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        gpu_frame.columnconfigure(0, weight=1)
        gpu_frame.rowconfigure(0, weight=1)

    def build_alerts_tab(self):
        self.build_clock_bar(self.alerts_tab, layout="grid")

        form = ttk.Frame(self.alerts_tab)
        form.grid(row=1, column=0, sticky="ew")
        self.alerts_tab.columnconfigure(0, weight=1)

        ttk.Label(form, text="First Name").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.alert_first_name_var).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=5,
        )

        ttk.Label(form, text="Last Name").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.alert_last_name_var).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=5,
        )

        ttk.Label(form, text="Email").grid(row=2, column=0, sticky="w", pady=5)
        self.alert_email_entry = ttk.Entry(
            form,
            textvariable=self.alert_email_var,
        )
        self.alert_email_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.alert_email_entry.bind("<Return>", lambda _event: self.send_test_alert_email())

        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self.alerts_tab)
        buttons.grid(row=2, column=0, sticky="w", pady=(18, 8))

        ttk.Button(
            buttons,
            text="Send Test Email",
            command=self.send_test_alert_email,
        ).grid(row=0, column=0, padx=(0, 8))

        self.alert_status_label = tk.Label(
            self.alerts_tab,
            textvariable=self.alert_status_var,
            anchor="w",
            fg="black",
        )
        self.alert_status_label.grid(row=3, column=0, sticky="ew")

    def build_db_tab(self):
        self.build_clock_bar(self.db_tab, layout="grid")

        form = ttk.Frame(self.db_tab)
        form.grid(row=1, column=0, sticky="ew")
        self.db_tab.columnconfigure(0, weight=1)
        self.db_tab.rowconfigure(3, weight=1)

        ttk.Label(form, text="SSH").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Label(
            form,
            text=f"{DB_SSH_USERNAME}@{DB_SSH_HOST}:{DB_SSH_PORT}",
        ).grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(form, text="Database").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Label(form, text=DB_NAME).grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(form, text="SSH Password").grid(row=2, column=0, sticky="w", pady=5)
        self.db_password_entry = ttk.Entry(
            form,
            textvariable=self.db_password_var,
            show="*",
        )
        self.db_password_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.db_password_entry.bind("<Return>", lambda _event: self.refresh_db_jobs())

        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=1, sticky="w", pady=(12, 4))

        self.db_refresh_button = ttk.Button(
            buttons,
            text="Refresh",
            command=self.refresh_db_jobs,
        )
        self.db_refresh_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(
            buttons,
            text="Clear Password",
            command=self.clear_db_password,
        ).grid(row=0, column=1, padx=(0, 8))

        form.columnconfigure(1, weight=1)

        self.db_status_label = tk.Label(
            self.db_tab,
            textvariable=self.db_status_var,
            anchor="w",
            fg="black",
        )
        self.db_status_label.grid(row=2, column=0, sticky="ew", pady=(10, 8))

        table_frame = ttk.Frame(self.db_tab)
        table_frame.grid(row=3, column=0, sticky="nsew")

        self.db_jobs_tree = ttk.Treeview(
            table_frame,
            columns=("state", "timeselapsed", "model", "image"),
            show="headings",
        )
        self.db_jobs_tree.heading("state", text="State")
        self.db_jobs_tree.heading("timeselapsed", text="Time Elapsed")
        self.db_jobs_tree.heading("model", text="Model")
        self.db_jobs_tree.heading("image", text="Image")
        self.db_jobs_tree.column("state", width=110, minwidth=90, stretch=False)
        self.db_jobs_tree.column("timeselapsed", width=120, minwidth=100, stretch=False)
        self.db_jobs_tree.column("model", width=360, minwidth=180, stretch=True)
        self.db_jobs_tree.column("image", width=520, minwidth=220, stretch=True)
        self.db_jobs_tree.tag_configure("running", background="#15803d", foreground="white")

        yscrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.db_jobs_tree.yview,
        )
        xscrollbar = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=self.db_jobs_tree.xview,
        )
        self.db_jobs_tree.configure(
            yscrollcommand=yscrollbar.set,
            xscrollcommand=xscrollbar.set,
        )

        self.db_jobs_tree.grid(row=0, column=0, sticky="nsew")
        yscrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

    def update_checkerboard_scroll_region(self, _event=None):
        self.container_canvas.configure(scrollregion=self.container_canvas.bbox("all"))

    def resize_checkerboard(self, event):
        self.container_canvas.itemconfigure(self.container_grid_window, width=event.width)
        if self.last_containers is not None:
            self.schedule_container_resize()

    def scroll_checkerboard(self, event):
        self.container_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def update_gpu_scroll_region(self, _event=None):
        self.gpu_canvas.configure(scrollregion=self.gpu_canvas.bbox("all"))

    def resize_gpu_grid(self, event):
        self.gpu_canvas.itemconfigure(self.gpu_grid_window, width=event.width)
        if self.last_gpus is not None:
            self.schedule_gpu_resize()

    def scroll_gpu_grid(self, event):
        self.gpu_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def schedule_container_resize(self):
        if self.container_resize_id is not None:
            self.root.after_cancel(self.container_resize_id)

        self.container_resize_id = self.root.after(100, self.render_container_tiles)

    def schedule_gpu_resize(self):
        if self.gpu_resize_id is not None:
            self.root.after_cancel(self.gpu_resize_id)

        self.gpu_resize_id = self.root.after(100, self.render_gpu_tiles)

    def grid_layout(self, canvas_width, preferred_width, min_width, max_columns):
        canvas_width = max(canvas_width, min_width)
        columns = max(1, min(max_columns, canvas_width // preferred_width))
        tile_width = max(min_width, (canvas_width - (columns * 12)) // columns)
        return columns, tile_width

    def add_container_tile(self, row, column, container, tile_width, tile_height):
        state = container_state(container)
        healthy = container_is_healthy(container)
        health_label = "HEALTHY" if healthy else "UNHEALTHY"
        tile_color = "#15803d" if healthy else "#b91c1c"
        detail_color = "#dcfce7" if healthy else "#fee2e2"
        wraplength = max(140, tile_width - 20)

        tile = tk.Frame(
            self.container_grid,
            bg=tile_color,
            bd=1,
            relief="solid",
            width=tile_width,
            height=tile_height,
        )
        tile.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        tile.grid_propagate(False)

        tk.Label(
            tile,
            text=health_label,
            bg=tile_color,
            fg="white",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 1))

        tk.Label(
            tile,
            text=container.get("Names", "<unknown>"),
            bg=tile_color,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=wraplength,
        ).pack(fill="x", padx=10)

        tk.Label(
            tile,
            text=f"State: {state}",
            bg=tile_color,
            fg=detail_color,
            anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 0))

        tk.Label(
            tile,
            text=container.get("Status", ""),
            bg=tile_color,
            fg=detail_color,
            anchor="w",
            justify="left",
            wraplength=wraplength,
        ).pack(fill="x", padx=10)

    def add_empty_container_tile(self, tile_width, tile_height):
        wraplength = max(140, tile_width - 20)
        tile = tk.Frame(
            self.container_grid,
            bg="#6b7280",
            bd=1,
            relief="solid",
            width=tile_width,
            height=tile_height,
        )
        tile.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        tile.grid_propagate(False)

        tk.Label(
            tile,
            text="NO CONTAINERS",
            bg="#6b7280",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 1))

        tk.Label(
            tile,
            text="No Docker containers were returned.",
            bg="#6b7280",
            fg="#f3f4f6",
            anchor="w",
            justify="left",
            wraplength=wraplength,
        ).pack(fill="x", padx=10)

    def add_metric_bar(self, parent, label, value_text, ratio, bar_color, bar_width):
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=10, pady=(5, 0))

        tk.Label(
            row,
            text=label,
            bg=parent["bg"],
            fg="#f9fafb",
            anchor="w",
        ).pack(side="left")

        tk.Label(
            row,
            text=value_text,
            bg=parent["bg"],
            fg="#f9fafb",
            anchor="e",
        ).pack(side="right")

        bar = tk.Canvas(parent, width=bar_width, height=9, bg="#111827", highlightthickness=0)
        bar.pack(fill="x", padx=10, pady=(1, 0))

        if ratio is not None:
            bar.create_rectangle(0, 0, int(bar_width * ratio), 9, fill=bar_color, width=0)

    def add_gpu_tile(self, row, column, gpu, tile_width, tile_height):
        healthy = gpu_is_healthy(gpu)
        status_label = "OK" if healthy else "HOT"
        tile_color = "#155e75" if healthy else "#b91c1c"
        bar_color = "#22c55e" if healthy else "#f97316"
        memory_ratio = metric_ratio(gpu.get("memory_used"), gpu.get("memory_total"))
        power_ratio = metric_ratio(gpu.get("power_draw"), gpu.get("power_limit"))
        utilization_ratio = metric_ratio(gpu.get("utilization"), 100)
        wraplength = max(180, tile_width - 20)
        bar_width = max(80, tile_width - 20)

        tile = tk.Frame(
            self.gpu_grid,
            bg=tile_color,
            bd=1,
            relief="solid",
            width=tile_width,
            height=tile_height,
        )
        tile.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        tile.grid_propagate(False)

        tk.Label(
            tile,
            text=f"GPU {gpu.get('index', '?')} - {status_label}",
            bg=tile_color,
            fg="white",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 1))

        tk.Label(
            tile,
            text=gpu.get("name", "unknown"),
            bg=tile_color,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=wraplength,
        ).pack(fill="x", padx=10)

        tk.Label(
            tile,
            text=f"Temp: {format_metric(gpu.get('temperature'), 'C')}",
            bg=tile_color,
            fg="#e0f2fe",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(5, 0))

        self.add_metric_bar(
            tile,
            "Utilization",
            format_metric(gpu.get("utilization"), "%"),
            utilization_ratio,
            bar_color,
            bar_width,
        )
        self.add_metric_bar(
            tile,
            "Memory",
            f"{format_metric(gpu.get('memory_used'), 'MiB')} / "
            f"{format_metric(gpu.get('memory_total'), 'MiB')}",
            memory_ratio,
            bar_color,
            bar_width,
        )
        self.add_metric_bar(
            tile,
            "Power",
            f"{format_metric(gpu.get('power_draw'), 'W')} / "
            f"{format_metric(gpu.get('power_limit'), 'W')}",
            power_ratio,
            bar_color,
            bar_width,
        )

    def add_gpu_unavailable_tile(self, tile_width, tile_height):
        wraplength = max(180, tile_width - 20)
        tile = tk.Frame(
            self.gpu_grid,
            bg="#6b7280",
            bd=1,
            relief="solid",
            width=tile_width,
            height=tile_height,
        )
        tile.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        tile.grid_propagate(False)

        tk.Label(
            tile,
            text="GPU UNAVAILABLE",
            bg="#6b7280",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 1))

        tk.Label(
            tile,
            text="nvidia-smi was not found or did not return GPU data.",
            bg="#6b7280",
            fg="#f3f4f6",
            anchor="w",
            justify="left",
            wraplength=wraplength,
        ).pack(fill="x", padx=10)

    def refresh_db_jobs(self):
        if self.db_query_running:
            return

        password = self.current_db_password()
        if not password:
            self.notebook.select(self.db_tab)
            self.set_db_status(
                "Enter the reveal SSH password before refreshing jobs.",
                "red",
            )
            self.db_password_entry.focus_set()
            return

        self.db_query_running = True
        self.db_refresh_button.configure(state="disabled")
        self.set_db_status(
            f"Querying {DB_NAME} on {DB_SSH_USERNAME}@{DB_SSH_HOST}...",
            "black",
        )

        thread = threading.Thread(
            target=self.db_jobs_worker,
            args=(password,),
            daemon=True,
        )
        thread.start()

    def current_db_password(self):
        password = self.db_password_var.get()
        if password:
            return password

        return os.getenv(DB_SSH_PASSWORD_ENV)

    def db_jobs_worker(self, password):
        try:
            command = db_jobs_command()
            exit_code, output, error = run_remote_command(
                command,
                host=DB_SSH_HOST,
                port=DB_SSH_PORT,
                username=DB_SSH_USERNAME,
                password=password,
            )
            result = {
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "command": command,
                "output": output,
                "error": error,
            }

            if exit_code == 0:
                result["rows"] = parse_db_jobs_output(output)
        except Exception as exc:
            result = {
                "ok": False,
                "exception": str(exc),
            }

        try:
            self.root.after(0, lambda: self.finish_db_jobs_query(result))
        except RuntimeError:
            pass

    def finish_db_jobs_query(self, result):
        self.db_query_running = False
        self.db_refresh_button.configure(state="normal")

        if not result["ok"]:
            message = remote_command_failure_message(result)
            if "SSH authentication failed" in message:
                self.clear_db_password(update_status=False)

            self.set_db_status(f"DB query failed: {message}", "red")
            return

        rows = result["rows"]
        self.render_db_jobs(rows)

        error = result.get("error", "").strip()
        if error:
            self.set_db_status(
                f"Loaded {len(rows)} job rows. Remote stderr: {error}",
                "black",
            )
        else:
            self.set_db_status(f"Loaded {len(rows)} job rows.", "green")

    def render_db_jobs(self, rows):
        for item in self.db_jobs_tree.get_children():
            self.db_jobs_tree.delete(item)

        model_values = []
        for row in rows:
            state = row.get("state", "")
            model = path_basename(row.get("model", ""))
            image = path_basename(row.get("image", ""))
            model_values.append(model)

            self.db_jobs_tree.insert(
                "",
                "end",
                values=(
                    state,
                    format_elapsed(row.get("timeselapsed", "")),
                    model,
                    image,
                ),
                tags=("running",) if str(state).lower() == "running" else (),
            )

        self.resize_db_model_column(model_values)

    def resize_db_model_column(self, model_values):
        values = ["Model", *model_values]
        if tkfont is not None:
            font = tkfont.nametofont("TkDefaultFont")
            width = max(font.measure(value) for value in values) + 32
        else:
            width = max(len(value) for value in values) * 8 + 32

        width = max(180, width)
        self.db_jobs_tree.column("model", width=width, minwidth=width)

    def set_db_status(self, message, color):
        self.db_status_var.set(message)
        self.db_status_label.configure(fg=color)

    def clear_db_password(self, update_status=True):
        self.db_password_var.set("")
        if update_status:
            self.set_db_status("Reveal SSH password cleared.", "black")
        self.db_password_entry.focus_set()

    def check_now(self):
        self.cancel_next_check()
        self.start_check()

    def start_check(self):
        if self.check_running:
            return

        host = self.current_host()
        username = self.username_var.get().strip()
        password = self.current_password()

        if not username:
            self.notebook.select(self.connection_tab)
            self.set_connection_status("Enter a username before checking.", "red")
            self.username_entry.focus_set()
            return

        if not password:
            self.notebook.select(self.connection_tab)
            self.set_connection_status("Enter the SSH password before checking.", "red")
            self.password_entry.focus_set()
            return

        self.check_running = True
        self.check_button.configure(state="disabled")
        self.set_connection_status(
            f"Checking Docker and GPU status on {host}...",
            "black",
        )

        thread = threading.Thread(
            target=self.check_worker,
            args=(host, username, password),
            daemon=True,
        )
        thread.start()

    def current_host(self):
        selection = self.selected_host_var.get()
        return HOST_BY_CHOICE.get(selection, HOST)

    def current_password(self):
        password = self.password_var.get()
        if password:
            return password

        if PASSWORD:
            return PASSWORD

        env_password = os.getenv("SSH_PASSWORD")
        if env_password:
            return env_password

        return CACHED_PASSWORD

    def check_worker(self, host, username, password):
        try:
            exit_code, output, error = run_remote_command(
                REMOTE_CMD,
                host=host,
                username=username,
                password=password,
            )
            result = {
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "command": REMOTE_CMD,
                "error": error,
                "output": output,
            }

            if exit_code == 0:
                info, containers, gpus, gpu_available = parse_output(output)
                result["info"] = info
                result["containers"] = containers
                result["gpus"] = gpus
                result["gpu_available"] = gpu_available
                result["docker_status"] = docker_health(containers)
        except Exception as exc:
            result = {
                "ok": False,
                "exception": str(exc),
            }

        try:
            self.root.after(0, lambda: self.finish_check(result))
        except RuntimeError:
            pass

    def finish_check(self, result):
        self.check_running = False
        self.check_button.configure(state="normal")

        if not result["ok"]:
            message = remote_command_failure_message(result)
            auth_failed = "SSH authentication failed" in message

            if auth_failed:
                self.clear_password(update_status=False)

            self.set_connection_status(f"Check failed: {message}", "red")
            self.set_docker_status("UNAVAILABLE")
            self.update_gpu_status([], False)

            if WATCH and not auth_failed:
                self.schedule_next_check()

            return

        error = result.get("error", "").strip()
        if error:
            self.set_connection_status(f"Check complete. Remote stderr: {error}", "black")
        else:
            self.set_connection_status("Check complete.", "green")

        self.update_container_status(
            result["info"],
            result["containers"],
            result["docker_status"],
        )
        self.update_gpu_status(
            result["gpus"],
            result["gpu_available"],
        )

        if self.notebook.select() == str(self.connection_tab):
            self.notebook.select(self.containers_tab)

        if WATCH:
            self.schedule_next_check()

    def update_container_status(self, info, containers, docker_status):
        self.set_docker_status(docker_status)
        self.host_var.set(f"Host: {info.get('Name', 'unknown')}")
        self.version_var.set(f"Version: {info.get('ServerVersion', 'unknown')}")
        self.images_var.set(f"Images: {info.get('Images', 'unknown')}")
        self.container_count_var.set(
            "Containers: "
            f"total={info.get('Containers', len(containers))}, "
            f"running={info.get('ContainersRunning', 'unknown')}, "
            f"paused={info.get('ContainersPaused', 'unknown')}, "
            f"stopped={info.get('ContainersStopped', 'unknown')}"
        )
        self.process_container_alerts(info, containers)
        self.last_containers = containers
        self.render_container_tiles()

    def render_container_tiles(self):
        self.container_resize_id = None

        for child in self.container_grid.winfo_children():
            child.destroy()

        canvas_width = max(self.container_canvas.winfo_width(), 1)
        columns, tile_width = self.grid_layout(
            canvas_width,
            preferred_width=220,
            min_width=180,
            max_columns=5,
        )
        tile_height = max(128, min(170, int(tile_width * 0.58)))

        for column in range(5):
            self.container_grid.columnconfigure(column, weight=0, minsize=0)

        for column in range(columns):
            self.container_grid.columnconfigure(column, weight=1, minsize=tile_width)

        containers = self.last_containers or []
        if not containers:
            self.add_empty_container_tile(tile_width, tile_height)
            return

        for index, container in enumerate(containers):
            row, column = divmod(index, columns)
            self.add_container_tile(row, column, container, tile_width, tile_height)

    def alert_contact(self, update_status=True):
        first_name = self.alert_first_name_var.get().strip()
        last_name = self.alert_last_name_var.get().strip()
        email = self.alert_email_var.get().strip()

        if not first_name or not last_name or not email:
            if update_status:
                self.set_alert_status(
                    "Enter first name, last name, and email before alerts can send.",
                    "red",
                )
            return None

        if not valid_email_address(email):
            if update_status:
                self.set_alert_status("Enter a valid email address.", "red")
                self.alert_email_entry.focus_set()
            return None

        return first_name, last_name, email

    def send_test_alert_email(self):
        contact = self.alert_contact()
        if not contact:
            return

        first_name, last_name, email = contact
        recipient_name = f"{first_name} {last_name}"
        checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        body = (
            f"Hello {recipient_name},\n\n"
            "This is a test email from PE Monitor.\n\n"
            f"SSH host: {self.current_host()}\n"
            f"Sent: {checked_at}\n"
        )

        self.set_alert_status(f"Sending test email to {email}...", "black")
        self.send_email_async(
            email,
            "PE Monitor Test Alert",
            body,
            recipient_name,
            f"Test email sent to {email}.",
            "Test email failed",
        )

    def send_email_async(
        self,
        to_address,
        subject,
        body,
        recipient_name,
        success_message,
        failure_prefix,
    ):
        def worker():
            try:
                send_email(to_address, subject, body, recipient_name)
                error = None
            except Exception as exc:
                error = str(exc)

            try:
                self.root.after(
                    0,
                    lambda: self.finish_email_send(
                        error,
                        success_message,
                        failure_prefix,
                    ),
                )
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def finish_email_send(self, error, success_message, failure_prefix):
        if error:
            self.set_alert_status(f"{failure_prefix}: {error}", "red")
            return

        self.set_alert_status(success_message, "green")

    def container_alert_key(self, container):
        return (
            container.get("ID")
            or container.get("Names")
            or container.get("Image")
            or json.dumps(container, sort_keys=True)
        )

    def container_alert_entry(self, container):
        return {
            "key": self.container_alert_key(container),
            "name": container.get("Names", "<unknown>"),
            "image": container.get("Image", "<unknown>"),
            "state": container_state(container),
            "status": container.get("Status", ""),
            "healthy": container_is_healthy(container),
        }

    def process_container_alerts(self, info, containers):
        current_snapshot = {
            self.container_alert_key(container): self.container_alert_entry(container)
            for container in containers
        }
        previous_snapshot = self.container_alert_snapshot
        down_events = []

        if previous_snapshot is None:
            down_events = [
                entry for entry in current_snapshot.values() if not entry["healthy"]
            ]
        else:
            for key, entry in current_snapshot.items():
                previous_entry = previous_snapshot.get(key)
                if previous_entry and previous_entry["healthy"] and not entry["healthy"]:
                    down_events.append(entry)

            for key, previous_entry in previous_snapshot.items():
                if key in current_snapshot or not previous_entry["healthy"]:
                    continue

                down_events.append(
                    {
                        **previous_entry,
                        "state": "MISSING",
                        "status": "Container no longer returned by docker ps -a.",
                        "healthy": False,
                    }
                )

        if down_events and not self.send_container_down_alert(info, down_events):
            return

        self.container_alert_snapshot = current_snapshot

    def send_container_down_alert(self, info, down_events):
        contact = self.alert_contact()
        if not contact:
            return False

        first_name, last_name, email = contact
        recipient_name = f"{first_name} {last_name}"
        host_name = info.get("Name", "unknown")
        subject = self.container_down_subject(host_name, down_events)
        body = self.container_down_body(recipient_name, host_name, down_events)

        self.set_alert_status(f"Sending container alert to {email}...", "black")
        self.send_email_async(
            email,
            subject,
            body,
            recipient_name,
            f"Container alert sent to {email}.",
            "Container alert failed",
        )
        return True

    def container_down_subject(self, host_name, down_events):
        container_word = "container" if len(down_events) == 1 else "containers"
        return (
            f"PE Monitor Alert: {len(down_events)} {container_word} down "
            f"on {host_name}"
        )

    def container_down_body(self, recipient_name, host_name, down_events):
        checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        lines = [
            f"Hello {recipient_name},",
            "",
            "PE Monitor detected container status problems.",
            "",
            f"SSH host: {self.current_host()}",
            f"Docker host: {host_name}",
            f"Checked: {checked_at}",
            "",
            "Affected containers:",
        ]

        for entry in down_events:
            lines.append(
                f"- {entry['name']} | State: {entry['state']} | "
                f"Status: {entry['status']}"
            )

        lines.extend(["", "This message was generated by PE Monitor."])
        return "\n".join(lines)

    def update_gpu_status(self, gpus, gpu_available):
        if not gpu_available:
            self.set_gpu_status("UNAVAILABLE")
            self.gpu_count_var.set("GPUs: unavailable")
        else:
            hot_count = sum(1 for gpu in gpus if not gpu_is_healthy(gpu))
            self.set_gpu_status("HOT" if hot_count else "OK")
            self.gpu_count_var.set(f"GPUs: {len(gpus)}")
        self.last_gpus = gpus
        self.last_gpu_available = gpu_available

        self.render_gpu_tiles()

    def render_gpu_tiles(self):
        self.gpu_resize_id = None

        for child in self.gpu_grid.winfo_children():
            child.destroy()

        canvas_width = max(self.gpu_canvas.winfo_width(), 1)
        columns, tile_width = self.grid_layout(
            canvas_width,
            preferred_width=310,
            min_width=260,
            max_columns=3,
        )
        tile_height = max(230, min(270, int(tile_width * 0.75)))

        for column in range(3):
            self.gpu_grid.columnconfigure(column, weight=0, minsize=0)

        for column in range(columns):
            self.gpu_grid.columnconfigure(column, weight=1, minsize=tile_width)

        if not self.last_gpu_available:
            self.add_gpu_unavailable_tile(tile_width, min(tile_height, 170))
            return

        gpus = self.last_gpus or []
        for index, gpu in enumerate(gpus):
            row, column = divmod(index, columns)
            self.add_gpu_tile(row, column, gpu, tile_width, tile_height)

    def set_connection_status(self, message, color):
        self.connection_status_var.set(message)
        self.connection_status_label.configure(fg=color)

    def set_alert_status(self, message, color):
        self.alert_status_var.set(message)
        self.alert_status_label.configure(fg=color)

    def set_docker_status(self, status):
        self.docker_status_var.set(f"Docker status: {status}")
        if status == "HEALTHY":
            self.docker_status_label.configure(fg="green")
        elif status == "UNAVAILABLE":
            self.docker_status_label.configure(fg="red")
        else:
            self.docker_status_label.configure(fg="red")

    def set_gpu_status(self, status):
        self.gpu_status_var.set(f"GPU status: {status}")
        if status == "OK":
            self.gpu_status_label.configure(fg="green")
        else:
            self.gpu_status_label.configure(fg="red")

    def schedule_next_check(self):
        self.cancel_next_check()
        self.next_check_id = self.root.after(INTERVAL_SECONDS * 1000, self.start_check)

    def cancel_next_check(self):
        if self.next_check_id is None:
            return

        self.root.after_cancel(self.next_check_id)
        self.next_check_id = None

    def clear_password(self, update_status=True):
        self.password_var.set("")
        clear_cached_password()
        if update_status:
            self.set_connection_status("Password cleared.", "black")
        self.password_entry.focus_set()


def run_tk_app():
    if tk is None or ttk is None:
        return False

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Tkinter UI unavailable: {exc}")
        return False

    DockerMonitorApp(root)
    root.mainloop()
    return True


def main():
    if run_tk_app():
        return

    if WATCH:
        while True:
            check_docker_status()
            time.sleep(INTERVAL_SECONDS)
    else:
        check_docker_status()


if __name__ == "__main__":
    main()
