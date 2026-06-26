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

HOST = "10.194.78.12"
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
    "printf '%s\\n' '__DOCKER_INFO__'; "
    "docker info --format '{{json .}}'; "
    "printf '%s\\n' '__DOCKER_PS__'; "
    "docker ps -a --format '{{json .}}'"
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


def get_ssh_password(username=USERNAME):
    global CACHED_PASSWORD

    if PASSWORD:
        return PASSWORD

    if CACHED_PASSWORD:
        return CACHED_PASSWORD

    env_password = os.getenv("SSH_PASSWORD")
    if env_password:
        return env_password

    prompt = f"Password for {username}@{HOST}: "

    CACHED_PASSWORD = read_masked_password(prompt)
    return CACHED_PASSWORD


def clear_cached_password():
    global CACHED_PASSWORD
    CACHED_PASSWORD = None


def run_remote_command(command, username=None, password=None):
    username = username or USERNAME
    password = password or get_ssh_password(username)

    print(f"Connecting to {username}@{HOST}:{PORT}...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=HOST,
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


def docker_health(containers):
    has_unhealthy_container = any(
        container_state(container) == "UNHEALTHY" for container in containers
    )
    return "UNHEALTHY" if has_unhealthy_container else "HEALTHY"


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

    print("-" * 60)


class DockerMonitorApp:
    def __init__(self, root):
        self.root = root
        self.check_running = False
        self.next_check_id = None

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
        self.refresh_var = tk.StringVar(value=f"Auto refresh: {INTERVAL_SECONDS}s")

        self.root.title("PE Monitor")
        self.root.geometry("920x560")
        self.root.minsize(760, 440)

        self.build_ui()
        self.password_entry.focus_set()

    def build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.connection_tab = ttk.Frame(self.notebook, padding=18)
        self.containers_tab = ttk.Frame(self.notebook, padding=18)

        self.notebook.add(self.connection_tab, text="Connection")
        self.notebook.add(self.containers_tab, text="Containers")

        self.build_connection_tab()
        self.build_containers_tab()

    def build_connection_tab(self):
        form = ttk.Frame(self.connection_tab)
        form.grid(row=0, column=0, sticky="nw")
        self.connection_tab.columnconfigure(0, weight=1)

        ttk.Label(form, text="Host").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Label(form, text=f"{HOST}:{PORT}").grid(row=0, column=1, sticky="w", pady=5)

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

        table_frame = ttk.Frame(self.containers_tab)
        table_frame.pack(fill="both", expand=True)

        columns = ("state", "name", "image", "status")
        self.container_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=14,
        )
        self.container_tree.heading("state", text="State")
        self.container_tree.heading("name", text="Name")
        self.container_tree.heading("image", text="Image")
        self.container_tree.heading("status", text="Status")
        self.container_tree.column("state", width=105, anchor="w", stretch=False)
        self.container_tree.column("name", width=190, anchor="w")
        self.container_tree.column("image", width=230, anchor="w")
        self.container_tree.column("status", width=330, anchor="w")
        self.container_tree.tag_configure("healthy", foreground="green")
        self.container_tree.tag_configure("unhealthy", foreground="red")

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.container_tree.yview,
        )
        self.container_tree.configure(yscrollcommand=scrollbar.set)

        self.container_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

    def check_now(self):
        self.cancel_next_check()
        self.start_check()

    def start_check(self):
        if self.check_running:
            return

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
        self.set_connection_status("Checking Docker status...", "black")

        thread = threading.Thread(
            target=self.check_worker,
            args=(username, password),
            daemon=True,
        )
        thread.start()

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

    def check_worker(self, username, password):
        try:
            exit_code, output, error = run_remote_command(
                REMOTE_CMD,
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
                info, containers = parse_output(output)
                result["info"] = info
                result["containers"] = containers
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

        for row_id in self.container_tree.get_children():
            self.container_tree.delete(row_id)

        for container in containers:
            state = container_state(container)
            tag = "unhealthy" if state == "UNHEALTHY" else "healthy"
            self.container_tree.insert(
                "",
                "end",
                values=(
                    state,
                    container.get("Names", "<unknown>"),
                    container.get("Image", "<unknown>"),
                    container.get("Status", ""),
                ),
                tags=(tag,),
            )

        self.notebook.select(self.containers_tab)

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
