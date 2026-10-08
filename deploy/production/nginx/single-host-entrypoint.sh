#!/bin/sh
# Starts the proxy in its form behind a Cloudflare Tunnel (compose.single-host.yml makes this the
# command). The image's configuration is complete except for one address, which belongs to the
# deployment and not to the image: the tunnel container's address on the network it shares with the
# proxy. It is written to the tmpfs at /tmp, the only place this container can write.
#
#   QD_TUNNEL_PEER   an IPv4 address, e.g. 10.99.240.2
set -eu

peer="${QD_TUNNEL_PEER:-}"
# Exactly one address: one peer is believed, never a network.
if ! printf '%s' "$peer" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
    echo "QD_TUNNEL_PEER must be the IPv4 address of the tunnel's container (got: '${peer}')" >&2
    exit 64
fi

cat > /tmp/qd-tunnel-peer.conf << CONF
# Written by single-host-entrypoint.sh at start.
# The visitor's address is believed from this one peer and from nobody else.
set_real_ip_from ${peer};
real_ip_header CF-Connecting-IP;
real_ip_recursive off;
# \$realip_remote_addr is the address the connection really came from.
geo \$realip_remote_addr \$qd_from_tunnel {
    default 0;
    ${peer} 1;
}
CONF

exec nginx -c /etc/nginx/nginx.single-host.conf -g 'daemon off;'
