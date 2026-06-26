import csv
import json
import getpass
import os
import sys
import threading
import time
import paramiko

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None
    ttk = None

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

WATCH = True
INTERVAL_SECONDS = 30
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

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


def run_remote_command(command, username=None, password=None, host=None):
    host = host or HOST
    username = username or USERNAME
    password = password or get_ssh_password(username, host)

    print(f"Connecting to {username}@{host}:{PORT}...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=PORT,
            username=username,
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
        clear_cached_password()
        raise RuntimeError(
            "SSH authentication failed. Check USERNAME/PASSWORD and confirm the "
            "server allows password login for this account."
        ) from exc
    except paramiko.SSHException as exc:
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"SSH connection timed out while connecting to {host}:{PORT}.") from exc
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
        self.next_check_id = None

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

        self.root.title("PE Monitor")
        self.root.geometry("1058x741")
        self.root.minsize(874, 582)

        self.build_ui()
        self.password_entry.focus_set()

    def build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.connection_tab = ttk.Frame(self.notebook, padding=18)
        self.containers_tab = ttk.Frame(self.notebook, padding=18)
        self.gpus_tab = ttk.Frame(self.notebook, padding=18)

        self.notebook.add(self.connection_tab, text="Connection")
        self.notebook.add(self.containers_tab, text="Containers")
        self.notebook.add(self.gpus_tab, text="GPUs")

        self.build_connection_tab()
        self.build_containers_tab()
        self.build_gpus_tab()

    def build_connection_tab(self):
        form = ttk.Frame(self.connection_tab)
        form.grid(row=0, column=0, sticky="nw")
        self.connection_tab.columnconfigure(0, weight=1)

        ttk.Label(form, text="Host").grid(row=0, column=0, sticky="w", pady=5)
        self.host_combo = ttk.Combobox(
            form,
            textvariable=self.selected_host_var,
            values=HOST_CHOICES,
            state="readonly",
            width=34,
        )
        self.host_combo.grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Username").grid(row=1, column=0, sticky="w", pady=5)
        self.username_entry = ttk.Entry(form, textvariable=self.username_var, width=34)
        self.username_entry.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Password").grid(row=2, column=0, sticky="w", pady=5)
        self.password_entry = ttk.Entry(
            form,
            textvariable=self.password_var,
            show="*",
            width=34,
        )
        self.password_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.password_entry.bind("<Return>", lambda _event: self.check_now())

        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self.connection_tab)
        buttons.grid(row=1, column=0, sticky="w", pady=(18, 8))

        self.check_button = ttk.Button(buttons, text="Check Now", command=self.check_now)
        self.check_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(buttons, text="Clear Password", command=self.clear_password).grid(
            row=0,
            column=1,
        )

        ttk.Label(self.connection_tab, textvariable=self.refresh_var).grid(
            row=2,
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
        self.connection_status_label.grid(row=3, column=0, sticky="ew")

    def build_containers_tab(self):
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

    def update_checkerboard_scroll_region(self, _event=None):
        self.container_canvas.configure(scrollregion=self.container_canvas.bbox("all"))

    def resize_checkerboard(self, event):
        self.container_canvas.itemconfigure(self.container_grid_window, width=event.width)

    def scroll_checkerboard(self, event):
        self.container_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def update_gpu_scroll_region(self, _event=None):
        self.gpu_canvas.configure(scrollregion=self.gpu_canvas.bbox("all"))

    def resize_gpu_grid(self, event):
        self.gpu_canvas.itemconfigure(self.gpu_grid_window, width=event.width)

    def scroll_gpu_grid(self, event):
        self.gpu_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def add_container_tile(self, row, column, container):
        state = container_state(container)
        healthy = container_is_healthy(container)
        health_label = "HEALTHY" if healthy else "UNHEALTHY"
        tile_color = "#15803d" if healthy else "#b91c1c"
        detail_color = "#dcfce7" if healthy else "#fee2e2"

        tile = tk.Frame(
            self.container_grid,
            bg=tile_color,
            bd=1,
            relief="solid",
            width=210,
            height=128,
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
            wraplength=185,
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
            wraplength=185,
        ).pack(fill="x", padx=10)

    def add_empty_container_tile(self):
        tile = tk.Frame(
            self.container_grid,
            bg="#6b7280",
            bd=1,
            relief="solid",
            width=210,
            height=128,
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
            wraplength=185,
        ).pack(fill="x", padx=10)

    def add_metric_bar(self, parent, label, value_text, ratio, bar_color):
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=10, pady=(5, 0))

        tk.Label(
            row,
            text=label,
            bg=parent["bg"],
            fg="#f9fafb",
            anchor="w",
            width=11,
        ).pack(side="left")

        bar = tk.Canvas(row, width=115, height=9, bg="#111827", highlightthickness=0)
        bar.pack(side="left", padx=(4, 6))

        if ratio is not None:
            bar.create_rectangle(0, 0, int(115 * ratio), 9, fill=bar_color, width=0)

        tk.Label(
            row,
            text=value_text,
            bg=parent["bg"],
            fg="#f9fafb",
            anchor="w",
        ).pack(side="left")

    def add_gpu_tile(self, row, column, gpu):
        healthy = gpu_is_healthy(gpu)
        status_label = "OK" if healthy else "HOT"
        tile_color = "#155e75" if healthy else "#b91c1c"
        bar_color = "#22c55e" if healthy else "#f97316"
        memory_ratio = metric_ratio(gpu.get("memory_used"), gpu.get("memory_total"))
        power_ratio = metric_ratio(gpu.get("power_draw"), gpu.get("power_limit"))
        utilization_ratio = metric_ratio(gpu.get("utilization"), 100)

        tile = tk.Frame(
            self.gpu_grid,
            bg=tile_color,
            bd=1,
            relief="solid",
            width=290,
            height=198,
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
            wraplength=260,
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
        )
        self.add_metric_bar(
            tile,
            "Memory",
            f"{format_metric(gpu.get('memory_used'), 'MiB')} / "
            f"{format_metric(gpu.get('memory_total'), 'MiB')}",
            memory_ratio,
            bar_color,
        )
        self.add_metric_bar(
            tile,
            "Power",
            f"{format_metric(gpu.get('power_draw'), 'W')} / "
            f"{format_metric(gpu.get('power_limit'), 'W')}",
            power_ratio,
            bar_color,
        )

    def add_gpu_unavailable_tile(self):
        tile = tk.Frame(
            self.gpu_grid,
            bg="#6b7280",
            bd=1,
            relief="solid",
            width=290,
            height=150,
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
            wraplength=260,
        ).pack(fill="x", padx=10)

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
            message = result.get("exception") or f"Remote command exited with {result['exit_code']}."
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

        for child in self.container_grid.winfo_children():
            child.destroy()

        canvas_width = max(self.container_canvas.winfo_width(), 1)
        columns = max(2, min(5, canvas_width // 220))

        for column in range(5):
            self.container_grid.columnconfigure(column, weight=0, minsize=0)

        for column in range(columns):
            self.container_grid.columnconfigure(column, weight=1, minsize=210)

        if not containers:
            self.add_empty_container_tile()
            return

        for index, container in enumerate(containers):
            row, column = divmod(index, columns)
            self.add_container_tile(row, column, container)

    def update_gpu_status(self, gpus, gpu_available):
        if not gpu_available:
            self.set_gpu_status("UNAVAILABLE")
            self.gpu_count_var.set("GPUs: unavailable")
        else:
            hot_count = sum(1 for gpu in gpus if not gpu_is_healthy(gpu))
            self.set_gpu_status("HOT" if hot_count else "OK")
            self.gpu_count_var.set(f"GPUs: {len(gpus)}")

        for child in self.gpu_grid.winfo_children():
            child.destroy()

        canvas_width = max(self.gpu_canvas.winfo_width(), 1)
        columns = max(1, min(3, canvas_width // 300))

        for column in range(3):
            self.gpu_grid.columnconfigure(column, weight=0, minsize=0)

        for column in range(columns):
            self.gpu_grid.columnconfigure(column, weight=1, minsize=290)

        if not gpu_available:
            self.add_gpu_unavailable_tile()
            return

        for index, gpu in enumerate(gpus):
            row, column = divmod(index, columns)
            self.add_gpu_tile(row, column, gpu)

    def set_connection_status(self, message, color):
        self.connection_status_var.set(message)
        self.connection_status_label.configure(fg=color)

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
