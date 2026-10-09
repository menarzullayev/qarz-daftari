"""The files of the single-host deployment stay in step with each other.

`deploy/production/compose.single-host.yml` puts the whole service on one machine behind a Cloudflare
Tunnel, with its backups in a bucket. What is checked here needs no Docker: that every setting the
overlay asks for is named in an example file, that the proxy believes one address and never a network,
that both forms of the proxy share one set of limits and routes, that the repository is encrypted and
keeps what the two-server design keeps, and that every image is pinned. What needs a running stack is
proven by `deploy/production/scripts/single-host-proof.sh` in CI.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PRODUCTION = REPO / "deploy" / "production"
SINGLE = PRODUCTION / "single-host"
NGINX = PRODUCTION / "nginx"

# Set by scripts/single-host.sh for the commands that need them, never written in an env file.
SET_BY_THE_SCRIPTS = {"DEPLOY_PGBACKUP_TAG", "DEPLOY_PITR_TARGET"}
RETENTION = (
    "repo1-retention-full-type",
    "repo1-retention-full",
    "repo1-retention-archive-type",
    "repo1-retention-archive",
    "repo1-retention-diff",
    "expire-auto",
)


# Each calendar job: what its systemd timer of deploy/backup says, and the scheduler's line for it
# (days of the week counted from Sunday = 0).
SCHEDULE = {
    "qd-backup-full": ("Sun *-*-* 01:30:00 Asia/Tashkent", 'full) weekly_slot "$2" "0" 01:30 ;;'),
    "qd-backup-diff": ("Mon..Sat *-*-* 01:30:00 Asia/Tashkent", 'diff) weekly_slot "$2" "1 2 3 4 5 6" 01:30 ;;'),
    "qd-restore-test": ("Wed *-*-* 02:30:00 Asia/Tashkent", 'restore-test) weekly_slot "$2" "3" 02:30 ;;'),
    "qd-backup-expire": ("*-*-* 03:30:00 Asia/Tashkent", 'expire) weekly_slot "$2" "0 1 2 3 4 5 6" 03:30 ;;'),
    "qd-backup-monthly": ("*-*-01 04:30:00 Asia/Tashkent", 'monthly) monthly_slot "$2" 04:30 ;;'),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assigned_names(env_text: str) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_text, flags=re.MULTILINE))


def asked_names(compose_text: str) -> set[str]:
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_text))


def options(conf_text: str) -> dict[str, str]:
    """`key=value` lines of a pgBackRest configuration file; comments and sections are not options."""
    return dict(re.findall(r"^([a-z0-9-]+)=(.*)$", conf_text, flags=re.MULTILINE))


def trusted_peers(script_text: str) -> list[str]:
    """What the proxy's start script writes after `set_real_ip_from`."""
    return re.findall(r"^set_real_ip_from\s+([^;]+);", script_text, flags=re.MULTILINE)


def believes_one_address_only(script_text: str) -> bool:
    """One `set_real_ip_from`, of the checked variable, and a check that refuses anything but an address."""
    return trusted_peers(script_text) == ["${peer}"] and r"'^([0-9]{1,3}\.){3}[0-9]{1,3}$'" in script_text


def unpinned(images: list[str]) -> list[str]:
    """Images with no tag, with `latest`, or with a tag that names no version at all."""
    return [image for image in images if not re.search(r":[^:/]*\d[^:/]*$", image) or image.endswith(":latest")]


@pytest.fixture(scope="module")
def overlay() -> str:
    return read(PRODUCTION / "compose.single-host.yml")


@pytest.fixture(scope="module")
def examples() -> set[str]:
    return assigned_names(read(PRODUCTION / "single-host.env.example")) | assigned_names(
        read(PRODUCTION / ".env.example")
    )


def test_the_overlay_asks_for_no_name_the_examples_lack(overlay: str, examples: set[str]) -> None:
    assert asked_names(overlay) - examples == SET_BY_THE_SCRIPTS


def test_a_name_missing_from_the_example_is_reported(overlay: str) -> None:
    without = read(PRODUCTION / "single-host.env.example").replace("DEPLOY_BACKUP_PASSPHRASE=", "# gone")
    names = assigned_names(without) | assigned_names(read(PRODUCTION / ".env.example"))
    assert asked_names(overlay) - names == SET_BY_THE_SCRIPTS | {"DEPLOY_BACKUP_PASSPHRASE"}


def test_the_single_host_example_carries_no_value() -> None:
    example = read(PRODUCTION / "single-host.env.example")
    assert re.findall(r"^[A-Z][A-Z0-9_]*=.+$", example, flags=re.MULTILINE) == []


def test_the_script_writes_and_requires_only_names_the_examples_have(examples: set[str]) -> None:
    script = read(PRODUCTION / "scripts" / "single-host.sh")
    written = assigned_names(script[script.index('cat > "$target" <<EOF') : script.index("\nEOF\n")])
    required = set(re.search(r'^REQUIRED="([^"]+)"', script, flags=re.MULTILINE).group(1).split())  # type: ignore[union-attr]
    assert written and required
    assert written - examples == set()
    assert required - examples == set()
    # What cannot be generated must be asked for before anything starts.
    assert {"CLOUDFLARE_TUNNEL_TOKEN", "DEPLOY_R2_BUCKET", "DEPLOY_BACKUP_PASSPHRASE"} <= required


def test_the_proxy_believes_one_address_and_never_a_network() -> None:
    script = read(NGINX / "single-host-entrypoint.sh")
    assert believes_one_address_only(script)
    assert "real_ip_header CF-Connecting-IP;" in script
    # Nowhere else: the form that ends TLS itself believes nobody about the caller's address.
    for path in [NGINX / "nginx.conf", NGINX / "nginx.single-host.conf", *NGINX.glob("*/*.conf")]:
        assert "set_real_ip_from" not in re.sub(r"#.*", "", read(path)), path


@pytest.mark.parametrize(
    "broken",
    [
        "set_real_ip_from 0.0.0.0/0;",  # everybody
        "set_real_ip_from 10.99.240.0/28;",  # the whole network of the tunnel
        "set_real_ip_from ${peer};\nset_real_ip_from 172.16.0.0/12;",  # the tunnel, and every container beside it
    ],
)
def test_a_proxy_that_believes_more_than_the_tunnel_is_reported(broken: str) -> None:
    script = read(NGINX / "single-host-entrypoint.sh").replace("set_real_ip_from ${peer};", broken)
    assert not believes_one_address_only(script)


def test_a_proxy_that_does_not_check_the_address_is_reported() -> None:
    script = read(NGINX / "single-host-entrypoint.sh").replace(r"'^([0-9]{1,3}\.){3}[0-9]{1,3}$'", "'.'")
    assert not believes_one_address_only(script)


def test_both_forms_of_the_proxy_share_their_limits_routes_and_log() -> None:
    """A limit or a route changed for one form is changed for the other: they are the same files."""
    for main, server in (("nginx.conf", "conf.d/qarz.conf"), ("nginx.single-host.conf", "single-host.d/qarz.conf")):
        assert "include /etc/nginx/snippets/http-common.conf;" in read(NGINX / main)
        block = read(NGINX / server)
        assert "include /etc/nginx/snippets/limit-zones.conf;" in block
        assert "include /etc/nginx/snippets/app-server.conf;" in block
        # Neither defines a limit, a route to the API or a log format of its own.
        code = re.sub(r"#.*", "", block)
        assert not re.search(r"\b(limit_req_zone|limit_conn_zone|proxy_pass|log_format)\b", code), server
    zones = read(NGINX / "snippets" / "limit-zones.conf")
    assert set(re.findall(r"zone=(qd_[a-z]+):", zones)) == {
        "qd_general",
        "qd_auth",
        "qd_pay",
        "qd_share",
        "qd_webhook",
        "qd_static",
        "qd_conn",
    }
    # Every limit counts by the one address the proxy settled on.
    assert re.findall(r"^limit_(?:req|conn)_zone (\S+)", zones, flags=re.MULTILINE) == ["$binary_remote_addr"] * 7


def test_behind_the_tunnel_the_api_is_told_https_and_the_visitors_address() -> None:
    block = read(NGINX / "single-host.d" / "qarz.conf")
    assert "set $qd_forwarded_proto https;" in block
    assert 'map "$qd_from_tunnel:$http_x_forwarded_proto" $qd_visitor_https' in block
    assert "return 301 https://$host$request_uri;" in block
    assert "listen 8080 default_server;" in block and "ssl" not in re.sub(r"#.*", "", block)
    handed = read(NGINX / "snippets" / "api-proxy.conf")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in handed
    assert "proxy_set_header X-Forwarded-Proto $qd_forwarded_proto;" in handed
    assert "set $qd_forwarded_proto $scheme;" in read(NGINX / "conf.d" / "qarz.conf")
    assert https_is_believed_from(block) == ['"1:https"']


def https_is_believed_from(block: str) -> list[str]:
    """The keys of the map that says "this visitor spoke HTTPS": who says it, and what they said."""
    body = re.search(r"map \"\$qd_from_tunnel:\$http_x_forwarded_proto\" \$qd_visitor_https \{(.*?)\}", block, re.S)
    assert body is not None
    entries = [line.split() for line in re.sub(r"#.*", "", body.group(1)).splitlines() if line.strip()]
    return [key for key, value in entries if value == "1;"]


def test_a_scheme_believed_from_anybody_is_reported() -> None:
    block = read(NGINX / "single-host.d" / "qarz.conf").replace('"1:https" 1;', '"~:https$" 1;')
    assert https_is_believed_from(block) != ['"1:https"']


def test_the_repository_is_encrypted_and_in_a_bucket() -> None:
    conf = options(read(SINGLE / "postgres" / "pgbackrest.conf"))
    assert conf["repo1-type"] == "s3"
    assert conf["repo1-cipher-type"] == "aes-256-cbc"
    assert conf["repo1-s3-uri-style"] == "path"
    # Neither the key nor the passphrase is in a file of the repository.
    assert not [name for name in conf if "key" in name or "pass" in name]
    env = read(SINGLE / "scripts" / "env.sh")
    assert 'export PGBACKREST_REPO1_CIPHER_PASS="$QD_BACKUP_PASSPHRASE"' in env
    assert "RCLONE_CONFIG_QDCRYPT_TYPE=crypt" in env
    assert "RCLONE_CONFIG_QDCRYPT_FILENAME_ENCRYPTION=standard" in env


def test_the_single_host_keeps_what_the_two_server_design_keeps() -> None:
    """The retention figures of deploy/backup (DEC-060) are not restated differently here."""
    single = options(read(SINGLE / "postgres" / "pgbackrest.conf"))
    standby = options(read(REPO / "deploy" / "backup" / "pgbackrest.conf"))
    assert {name: single.get(name) for name in RETENTION} == {name: standby[name] for name in RETENTION}


def test_a_changed_retention_figure_is_reported() -> None:
    changed = options(read(SINGLE / "postgres" / "pgbackrest.conf").replace("retention-full=56", "retention-full=7"))
    standby = options(read(REPO / "deploy" / "backup" / "pgbackrest.conf"))
    assert changed["repo1-retention-full"] != standby["repo1-retention-full"]


def test_the_schedule_is_the_one_of_the_timers() -> None:
    """Each calendar job of the scheduler runs when its systemd timer of deploy/backup would."""
    scheduler = read(SINGLE / "scripts" / "scheduler.sh")
    for timer, (calendar, line) in SCHEDULE.items():
        unit = read(REPO / "deploy" / "backup" / "systemd" / f"{timer}.timer")
        assert f"OnCalendar={calendar}\n" in unit, timer
        assert line in scheduler, timer
    # The scheduler reads the clock in the same zone the timers name.
    assert "ENV TZ=Asia/Tashkent" in read(SINGLE / "postgres" / "Dockerfile")


def test_every_image_is_pinned() -> None:
    images = re.findall(r"^FROM (\S+)", read(SINGLE / "postgres" / "Dockerfile"), flags=re.MULTILINE)
    for compose in ("compose.single-host.yml", "compose.single-host.proof.yml"):
        images += [
            image
            for image in re.findall(r"^\s+image: (\S+)$", read(PRODUCTION / compose), flags=re.MULTILINE)
            if not image.startswith("${")  # the three images built from this repository, tagged by the scripts
        ]
    assert sorted(set(images)) == sorted(
        {"postgres:16.15-bookworm", "cloudflare/cloudflared:2026.9.3", "pgsty/minio:RELEASE.2026-08-04T00-00-00Z"}
    )
    assert unpinned(images) == []


def test_an_unpinned_image_is_reported() -> None:
    assert unpinned(["postgres", "postgres:latest", "cloudflare/cloudflared:stable", "postgres:16.15-bookworm"]) == [
        "postgres",
        "postgres:latest",
        "cloudflare/cloudflared:stable",
    ]


def _shares_a_temporary_name(script: str) -> list[str]:
    """Lines that write a file under a name every run of the script would use, to rename it afterwards."""
    return [line.strip() for line in script.splitlines() if re.search(r'>\s*"[^"]*\.tmp"|\bmv\b.*\.tmp"', line)]


def test_the_check_writes_its_status_under_a_name_of_its_own() -> None:
    """Checks run without the lock, two at a time now and then: a shared temporary name made one fail.

    The proof runs twelve at once (single-host-proof.sh, "checks-at-once"); this holds the script's text.
    """
    job = read(SINGLE / "scripts" / "job.sh")
    for script in sorted((SINGLE / "scripts").glob("*.sh")):
        assert _shares_a_temporary_name(read(script)) == [], script.name
    assert 'status="$(mktemp "$QD_STATE_DIR/.check.status.XXXXXX")"' in job
    assert 'mv -f "$status" "$QD_STATE_DIR/check.status"' in job
    # What the script was: both of its lines are found.
    before = (
        'printf \'%s %s\n\' "$code" "$(date +%s)" > "$QD_STATE_DIR/check.status.tmp"\n'
        'mv -f "$QD_STATE_DIR/check.status.tmp" "$QD_STATE_DIR/check.status"\n'
    )
    assert len(_shares_a_temporary_name(before)) == 2
    proof = read(PRODUCTION / "scripts" / "single-host-proof.sh")
    assert "step checks-at-once 0" in proof and "/opt/qarz-single/job.sh check --quiet > /dev/null &" in proof
