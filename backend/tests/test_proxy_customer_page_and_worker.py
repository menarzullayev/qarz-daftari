"""The proxy's rules for the page behind a customer's link and for the installable panel, read from its files.

deploy/production/scripts/smoke.sh checks the same against a running proxy; this holds the files to it
before an image is built, and has the case each check must refuse.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SNIPPETS = REPO / "deploy" / "production" / "nginx" / "snippets"
NGINX = SNIPPETS.parent
WORKER = REPO / "frontend" / "src" / "panel" / "pwa" / "worker.ts"


def read(name: str) -> str:
    return (SNIPPETS / name).read_text(encoding="utf-8")


def code(text: str) -> str:
    return re.sub(r"#.*", "", text)


def policy(text: str) -> dict[str, list[str]]:
    """The Content-Security-Policy a headers file sends, as directive -> sources."""
    found = re.findall(r'^add_header Content-Security-Policy "([^"]*)" always;', code(text), flags=re.MULTILINE)
    assert len(found) == 1, "one policy a file"
    return {part.split()[0]: part.split()[1:] for part in found[0].split(";") if part.strip()}


def header(text: str, name: str) -> list[str]:
    return re.findall(rf'^add_header {re.escape(name)} "?([^";]*)"? always;', code(text), flags=re.MULTILINE)


def location(text: str, opening: str) -> str:
    """The body of one `location` block of app-server.conf."""
    start = text.index(f"location {opening} {{")
    return text[start : text.index("\n}", start)]


# --- the installable panel -----------------------------------------------------------------------------


def test_the_panels_policy_allows_its_own_worker_and_manifest_and_nothing_more_than_before() -> None:
    panel, admin = policy(read("headers-panel.conf")), policy(read("headers-admin.conf"))
    assert panel["worker-src"] == ["'self'"] and panel["manifest-src"] == ["'self'"]
    # Everything else is the policy the two pages shared before the panel became installable.
    assert {name: sources for name, sources in panel.items() if name not in ("worker-src", "manifest-src")} == admin
    assert admin["default-src"] == ["'none'"] and admin["script-src"] == ["'self'", "https://telegram.org"]


@pytest.mark.parametrize(
    "name", ["headers-admin.conf", "headers-app.conf", "headers-customer.conf", "headers-api.conf"]
)
def test_no_other_page_may_register_a_worker_or_read_a_manifest(name: str) -> None:
    """With `default-src 'none'` and no directive of their own, both are refused."""
    found = policy(read(name))
    assert found["default-src"] == ["'none'"]
    assert "worker-src" not in found and "manifest-src" not in found


def allows_install(text: str) -> bool:
    found = policy(text)
    return found.get("worker-src") == ["'self'"] and found.get("manifest-src") == ["'self'"]


def test_the_install_check_refuses_a_policy_that_is_wider_or_missing() -> None:
    panel = read("headers-panel.conf")
    assert allows_install(panel)
    assert not allows_install(panel.replace("worker-src 'self'", "worker-src 'self' blob:"))
    assert not allows_install(panel.replace("manifest-src 'self'", "manifest-src *"))
    assert not allows_install(read("headers-admin.conf"))


def test_the_worker_is_served_from_the_panel_checked_every_time_and_never_given_a_wider_scope() -> None:
    server = read("app-server.conf")
    block = location(server, "= /panel/sw.js")
    assert "include /etc/nginx/snippets/headers-worker.conf;" in block
    assert "try_files $uri =404;" in block, "a missing worker is 404, never the panel's page as a script"
    worker = read("headers-worker.conf")
    assert header(worker, "Cache-Control") == ["no-cache"]
    assert "immutable" not in code(worker)
    assert policy(worker) == {"default-src": ["'none'"], "connect-src": ["'self'"], "frame-ancestors": ["'none'"]}
    # The header that would let a worker reach above its own directory is sent nowhere.
    for path in [*NGINX.glob("*.conf"), *NGINX.glob("*/*.conf")]:
        assert "service-worker-allowed" not in code(path.read_text(encoding="utf-8")).lower(), path


def test_the_manifest_is_served_as_a_manifest() -> None:
    block = location(read("app-server.conf"), "= /panel/manifest.webmanifest")
    assert "default_type application/manifest+json;" in block and "types { }" in block
    assert "try_files $uri =404;" in block


def test_only_the_panel_has_a_manifest_or_a_worker_route() -> None:
    server = code(read("app-server.conf"))
    assert re.findall(r"location = (\S*(?:manifest|sw)\S*) \{", server) == [
        "/panel/manifest.webmanifest",
        "/panel/sw.js",
    ]
    assert "include /etc/nginx/snippets/headers-admin.conf;" in location(server, "/admin/")
    assert "include /etc/nginx/snippets/headers-app.conf;" in location(server, "/app/")


# --- the worker never stands between a page and anything the proxy hands to the API ------------------------


def blocks(server: str) -> list[tuple[str, str, str]]:
    """Every `location` block: (kind of match, path, body), with the braces inside a body counted."""
    found = []
    text = code(server)
    for match in re.finditer(r'location (?:(=|~|\^~) )?"?([^\s"{]+)"? \{', text):
        depth, at = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[at], 0)
            at += 1
        found.append((match.group(1) or "", match.group(2), text[match.end() : at - 1]))
    return found


def proxied_prefixes(server: str) -> set[str]:
    """Every address the proxy hands to the API, as the prefix the worker must never handle."""
    prefixes = set()
    for kind, path, body in blocks(server):
        if "api-proxy.conf" not in body:
            continue
        path = path.lstrip("^")
        top = "/" + path.strip("/").split("/")[0]
        prefixes.add(top if kind == "=" and path.count("/") == 1 else top + "/")
    return prefixes


def never_handled() -> set[str]:
    source = WORKER.read_text(encoding="utf-8")
    listed = re.search(r"export const NEVER_HANDLED = \[([^\]]*)\] as const;", source)
    assert listed is not None
    return set(re.findall(r'"([^"]+)"', listed.group(1)))


def test_everything_the_proxy_hands_to_the_api_is_on_the_workers_list_of_what_it_never_touches() -> None:
    proxied = proxied_prefixes(read("app-server.conf"))
    assert {"/api/", "/files/", "/pay/", "/tg/", "/healthz"} <= proxied
    assert proxied <= never_handled()
    # And the other entries, which are static but not the worker's.
    assert {"/k/", "/admin/", "/app/"} <= never_handled()


def test_a_photo_of_the_shared_catalogue_may_be_cached_and_nothing_else_under_files_may() -> None:
    server = read("app-server.conf")
    assert "include /etc/nginx/snippets/api-proxy.conf;" in location(server, "/files/")
    assert header(read("headers-api.conf"), "Cache-Control") == ["no-store"]
    photos = location(server, "/files/catalog/")
    assert "include /etc/nginx/snippets/image-proxy.conf;" in photos and "api-proxy.conf" not in photos
    assert "limit_except GET" in photos
    handed = read("image-proxy.conf")
    # The application's own header is dropped and the proxy says it once, from the map.
    assert "proxy_hide_header Cache-Control;" in handed
    assert header(handed, "Cache-Control") == ["$qd_image_cache"]
    assert "headers-api.conf" not in handed, "that file says no-store, which would be said as well"
    assert image_cache(read("http-common.conf")) == {
        '"~immutable"': '"public, max-age=31536000, immutable"',
        "default": '"no-store"',
    }


def image_cache(http: str) -> dict[str, str]:
    """The map that decides what a cache is told about an answer of /files/catalog/."""
    body = re.search(r"map \$upstream_http_cache_control \$qd_image_cache \{(.*?)\}", code(http), re.S)
    assert body is not None
    entries = [line.strip().rstrip(";").split(" ", 1) for line in body.group(1).splitlines() if line.strip()]
    return {key: value.strip() for key, value in entries}


def test_a_map_that_let_a_refusal_be_cached_would_be_noticed() -> None:
    """The counterpart: with the default turned into "cache it", the same reading says so."""
    careless = read("http-common.conf").replace('default "no-store";', 'default "public, max-age=31536000";')
    assert image_cache(careless)["default"] != '"no-store"'


def test_the_check_above_would_notice_a_new_proxied_route() -> None:
    added = read("app-server.conf") + "\nlocation /reports/ {\n    include /etc/nginx/snippets/api-proxy.conf;\n}\n"
    assert proxied_prefixes(added) - never_handled() == {"/reports/"}


# --- the page behind a customer's link ---------------------------------------------------------------------


def test_the_customers_page_tells_nobody_anything_and_is_never_kept_or_listed() -> None:
    customer = read("headers-customer.conf")
    assert header(customer, "Referrer-Policy") == ["no-referrer"]
    assert header(customer, "X-Robots-Tag") == ["noindex, nofollow, noarchive"]
    assert header(customer, "Cache-Control") == ["no-store"]
    assert header(customer, "X-Frame-Options") == ["DENY"]
    assert header(customer, "X-Content-Type-Options") == ["nosniff"]
    assert header(customer, "Strict-Transport-Security") == ["max-age=31536000"]
    assert "include" not in code(customer), "every header is written here once; none is sent twice"
    # Every header of headers-common.conf is here too.
    common = set(re.findall(r"^add_header (\S+)", code(read("headers-common.conf")), flags=re.MULTILINE))
    assert common <= set(re.findall(r"^add_header (\S+)", code(customer), flags=re.MULTILINE))


def test_the_customers_page_reaches_its_own_origin_and_nothing_else() -> None:
    found = policy(read("headers-customer.conf"))
    assert found == {
        "default-src": ["'none'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "connect-src": ["'self'"],
        "base-uri": ["'none'"],
        "form-action": ["'none'"],
        "frame-ancestors": ["'none'"],
    }
    # No third party anywhere in what the page is sent, and nothing of the device.
    assert "http" not in code(read("headers-customer.conf")).replace("max-age", "")
    assert header(read("headers-customer.conf"), "Permissions-Policy")[0].endswith("clipboard-write=()")


def test_the_customers_page_is_served_with_those_headers_at_its_own_path() -> None:
    server = read("app-server.conf")
    block = location(server, "/k/")
    assert "include /etc/nginx/snippets/headers-customer.conf;" in block
    assert "try_files $uri $uri/ /k/index.html;" in block
    assert "return 301 /k/;" in location(server, "= /k")


def test_the_read_behind_a_link_has_its_own_limit_by_address_and_one_address() -> None:
    server = read("app-server.conf")
    block = location(server, "= /api/v1/customer-share")
    assert "limit_req zone=qd_share burst=20 nodelay;" in block
    assert "limit_req zone=qd_general burst=40 nodelay;" in block
    assert "include /etc/nginx/snippets/api-proxy.conf;" in block
    zone = re.findall(
        r"^limit_req_zone (\S+) zone=qd_share:\S+ rate=(\S+);", read("limit-zones.conf"), flags=re.MULTILINE
    )
    assert zone == [("$binary_remote_addr", "60r/m")]
    # An exact match: no address under it exists, so no secret can be put in a path and logged.
    assert "location /api/v1/customer-share" not in code(server).replace("location = /api/v1/customer-share", "")


def test_the_access_log_holds_no_header_and_no_query_string() -> None:
    """Where the secret travels (a header) and where a caller might put it by mistake (a query string)."""
    log = read("http-common.conf")
    written = re.search(r"log_format qd_json escape=json(.*?);", log, flags=re.DOTALL)
    assert written is not None
    assert (
        "$http_" not in written.group(1) and "$request_uri" not in written.group(1) and "$args" not in written.group(1)
    )
    assert '"path":"$qd_path"' in written.group(1)
