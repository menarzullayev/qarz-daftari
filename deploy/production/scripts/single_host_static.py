"""Static checks of the single-host composition (compose.yml + compose.single-host.yml).

    docker compose ... config --format json | python single_host_static.py            check it
    docker compose ... config --format json | python single_host_static.py --self-test

Reads the composition as Docker Compose resolved it and checks what the deployment promises without
starting anything: nothing connects anywhere, and no value of the env file is printed. `--self-test`
breaks a copy of the same composition once for each check and fails unless that check notices.

Standard library only. Exit status 0 when every check passed.
"""

import copy
import json
import re
import sys
from collections.abc import Callable
from typing import Any

Composition = dict[str, Any]

LONG_RUNNING = ("proxy", "api", "worker", "db", "cloudflared", "backup", "files-backup")
ADDED = ("db", "owner", "roles", "cloudflared", "backup", "files-backup", "restore", "restore-files", "db-scratch")
TUNNEL_COMMAND = ["tunnel", "--no-autoupdate", "--metrics", "127.0.0.1:20241", "run"]
MEMORY_BUDGET = 4 * 1024**3


def services(c: Composition) -> dict[str, Any]:
    return dict(c["services"])


def tunnel_is_pinned(c: Composition) -> str | None:
    image = services(c)["cloudflared"]["image"]
    if not re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", image):
        return f"the tunnel's image is not pinned to a version: {image}"
    return None


def tunnel_runs_from_a_token_in_the_environment(c: Composition) -> str | None:
    tunnel = services(c)["cloudflared"]
    if tunnel.get("command") != TUNNEL_COMMAND:
        return f"the tunnel's command is {tunnel.get('command')}"
    if any("token" in str(part).lower() for part in tunnel.get("command") or []):
        return "the token is on the command line"
    if not (tunnel.get("environment") or {}).get("TUNNEL_TOKEN"):
        return "TUNNEL_TOKEN is not handed to the tunnel"
    for name, service in services(c).items():
        if name != "cloudflared" and "TUNNEL_TOKEN" in (service.get("environment") or {}):
            return f"{name} is handed the tunnel's token"
    return None


def nothing_is_published(c: Composition) -> str | None:
    for name, service in services(c).items():
        if service.get("ports"):
            return f"{name} publishes a port on the host"
        if service.get("network_mode") == "host":
            return f"{name} uses the host's network"
    return None


def the_proxy_believes_the_tunnel_and_nobody_else(c: Composition) -> str | None:
    all_services = services(c)
    proxy, tunnel = all_services["proxy"], all_services["cloudflared"]
    address = ((tunnel.get("networks") or {}).get("tunnel") or {}).get("ipv4_address")
    peer = (proxy.get("environment") or {}).get("QD_TUNNEL_PEER")
    if not address or address != peer:
        return f"the proxy believes {peer}, the tunnel is at {address}"
    if proxy.get("command") != ["/usr/local/bin/qd-single-host-proxy"]:
        return "the proxy is not started in its form behind the tunnel"
    on_tunnel = sorted(name for name, s in all_services.items() if "tunnel" in (s.get("networks") or {}))
    if on_tunnel != ["cloudflared", "proxy"]:
        return f"on the tunnel's network: {on_tunnel}"
    if sorted(proxy.get("networks") or {}) != ["edge", "tunnel"]:
        return f"the proxy is on {sorted(proxy.get('networks') or {})}"
    if not c["networks"]["tunnel"].get("internal"):
        return "the tunnel's network is not internal"
    subnet = c["networks"]["tunnel"]["ipam"]["config"][0]
    if not subnet.get("ip_range"):
        return "the tunnel's network hands out the tunnel's own address to other containers"
    return None


def everything_comes_back_by_itself(c: Composition) -> str | None:
    for name in LONG_RUNNING:
        if services(c)[name].get("restart") != "unless-stopped":
            return f"{name} is not restarted (restart: {services(c)[name].get('restart')})"
    for name in ("db", "cloudflared", "backup", "files-backup"):
        check = services(c)[name].get("healthcheck") or {}
        if not check.get("test") or check.get("disable"):
            return f"{name} has no health check"
    for name, needs in (("migrate", "owner"), ("roles", "migrate"), ("api", "roles"), ("worker", "roles")):
        if needs not in (services(c)[name].get("depends_on") or {}):
            return f"{name} does not wait for {needs}"
    if (services(c)["cloudflared"].get("depends_on") or {}).get("proxy", {}).get("condition") != "service_healthy":
        return "the tunnel does not wait for the proxy"
    return None


def memory_fits(c: Composition) -> str | None:
    total = 0
    for name in LONG_RUNNING:
        limit = (((services(c)[name].get("deploy") or {}).get("resources") or {}).get("limits") or {}).get("memory")
        if not limit:
            return f"{name} has no memory limit"
        total += int(limit)
    if total > MEMORY_BUDGET:
        return f"the memory limits add up to {total / 1024**3:.2f} GiB, more than 4"
    return None


def logs_are_rotated(c: Composition) -> str | None:
    for name, service in services(c).items():
        options = (service.get("logging") or {}).get("options") or {}
        if not options.get("max-size") or not options.get("max-file"):
            return f"the log of {name} is not rotated"
    return None


def added_services_are_confined(c: Composition) -> str | None:
    for name in ADDED:
        service = services(c)[name]
        if not service.get("read_only"):
            return f"{name} has a writable root file system"
        if service.get("cap_drop") != ["ALL"]:
            return f"{name} keeps capabilities"
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            return f"{name} may gain privileges"
        if str(service.get("user", "")).split(":")[0] in ("0", "root"):
            return f"{name} runs as root"
    return None


def only_named_volumes(c: Composition) -> str | None:
    for name, service in services(c).items():
        for volume in service.get("volumes") or []:
            if volume.get("type") != "volume":
                return f"{name} mounts a path of the host: {volume.get('source')}"
    return None


def the_live_data_is_written_by_the_database_alone(c: Composition) -> str | None:
    writers = []
    for name, service in services(c).items():
        for volume in service.get("volumes") or []:
            if volume.get("source") == "pgdata" and not volume.get("read_only"):
                writers.append(name)
    # `restore` writes into the volume only while it is empty (restore.sh refuses otherwise).
    if sorted(writers) != ["db", "restore"]:
        return f"the data volume is writable by {sorted(writers)}"
    return None


def images_are_pinned(c: Composition) -> str | None:
    for name, service in services(c).items():
        image = service.get("image", "")
        tag = image.rsplit(":", 1)[1] if ":" in image else ""
        if not tag or tag == "latest":
            return f"the image of {name} is not pinned: {image}"
    return None


def backups_are_encrypted_and_scheduled(c: Composition) -> str | None:
    for name in ("db", "backup", "files-backup"):
        if not (services(c)[name].get("environment") or {}).get("QD_BACKUP_PASSPHRASE"):
            return f"{name} is not handed the backup passphrase"
    if services(c)["backup"].get("command") != ["scheduler"]:
        return "the backup service does not run the schedule"
    if services(c)["files-backup"].get("command") != ["files-loop"]:
        return "the files are not copied on a schedule"
    return None


Check = Callable[[Composition], str | None]


def break_by(change: Callable[[dict[str, Any]], None]) -> Callable[[Composition], None]:
    def apply(c: Composition) -> None:
        change(c["services"])

    return apply


def _add_writer(s: dict[str, Any]) -> None:
    for volume in s["backup"]["volumes"]:
        if volume["source"] == "pgdata":
            volume["read_only"] = False


def _publish(s: dict[str, Any]) -> None:
    s["proxy"]["ports"] = [{"target": 8080, "published": "80"}]


def _unbounded_range(c: Composition) -> None:
    del c["networks"]["tunnel"]["ipam"]["config"][0]["ip_range"]


# Each check with one way of breaking the composition that it must notice.
CHECKS: list[tuple[str, Check, Callable[[Composition], None]]] = [
    (
        "the tunnel's image is pinned to a version",
        tunnel_is_pinned,
        break_by(lambda s: s["cloudflared"].update(image="cloudflare/cloudflared:latest")),
    ),
    (
        "the tunnel runs from a token given in the environment, to it alone",
        tunnel_runs_from_a_token_in_the_environment,
        break_by(lambda s: s["cloudflared"].update(command=[*TUNNEL_COMMAND, "--token", "x"])),
    ),
    ("no service publishes a port on the host", nothing_is_published, break_by(_publish)),
    (
        "the proxy believes the tunnel's address and shares its network with nobody else",
        the_proxy_believes_the_tunnel_and_nobody_else,
        break_by(lambda s: s["proxy"]["environment"].update(QD_TUNNEL_PEER="10.99.240.9")),
    ),
    (
        "the tunnel's address cannot be handed to another container",
        the_proxy_believes_the_tunnel_and_nobody_else,
        _unbounded_range,
    ),
    (
        "every long-running service restarts, has its health check and waits for what it needs",
        everything_comes_back_by_itself,
        break_by(lambda s: s["db"].update(restart="no")),
    ),
    (
        "the memory limits fit in 4 GiB",
        memory_fits,
        break_by(lambda s: s["db"]["deploy"]["resources"]["limits"].update(memory=str(3 * 1024**3))),
    ),
    ("every log is rotated", logs_are_rotated, break_by(lambda s: s["db"].pop("logging"))),
    (
        "the added services are read-only, without capabilities and not root",
        added_services_are_confined,
        break_by(lambda s: s["backup"].update(user="0:0")),
    ),
    (
        "nothing of the host's file system is mounted",
        only_named_volumes,
        break_by(lambda s: s["proxy"].update(volumes=[{"type": "bind", "source": "/etc/qarz/tls"}])),
    ),
    (
        "the live data volume is writable by the database alone (and by a restore into an empty one)",
        the_live_data_is_written_by_the_database_alone,
        break_by(_add_writer),
    ),
    (
        "every image is pinned by a tag",
        images_are_pinned,
        break_by(lambda s: s["db"].update(image="qarz-daftari/pgbackup")),
    ),
    (
        "the backups are encrypted and on a schedule",
        backups_are_encrypted_and_scheduled,
        break_by(lambda s: s["db"]["environment"].pop("QD_BACKUP_PASSPHRASE")),
    ),
]


def main() -> int:
    self_test = "--self-test" in sys.argv[1:]
    composition: Composition = json.load(sys.stdin)
    failures = 0
    for description, check, break_it in CHECKS:
        if self_test:
            broken = copy.deepcopy(composition)
            break_it(broken)
            noticed = check(broken)
            if noticed:
                print(f"PASS  a broken composition is refused: {description} ({noticed})")
            else:
                print(f"FAIL  a broken composition passed: {description}")
                failures += 1
        else:
            problem = check(composition)
            if problem:
                print(f"FAIL  {description}: {problem}")
                failures += 1
            else:
                print(f"PASS  {description}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
