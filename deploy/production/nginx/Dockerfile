# syntax=docker/dockerfile:1
# The proxy image: nginx with its configuration and the three built front-end entries.
# Build context: the repository root (see /.dockerignore).

FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Public, build-time setting: the bot whose Telegram Login widget and deep links the pages use. It is
# part of the shipped script, so it is not a secret. Empty: the panel shows no sign-in widget.
ARG VITE_BOT_USERNAME=""
ENV VITE_BOT_USERNAME=$VITE_BOT_USERNAME
RUN npm run build

FROM nginx:1.30-alpine
# The stock entrypoint scripts edit the configuration at start; this image's configuration is complete.
RUN rm -rf /docker-entrypoint.d/* /etc/nginx/conf.d/* /usr/share/nginx/html/*
COPY deploy/production/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/production/nginx/conf.d/ /etc/nginx/conf.d/
COPY deploy/production/nginx/snippets/ /etc/nginx/snippets/
# The form behind a Cloudflare Tunnel (the single-host deployment): its own main file, server block and
# start script. Unused unless compose.single-host.yml makes the script the command.
COPY deploy/production/nginx/nginx.single-host.conf /etc/nginx/nginx.single-host.conf
COPY deploy/production/nginx/single-host.d/ /etc/nginx/single-host.d/
COPY --chmod=755 deploy/production/nginx/single-host-entrypoint.sh /usr/local/bin/qd-single-host-proxy
COPY --from=frontend /frontend/dist/ /usr/share/nginx/html/
# Both forms are checked at build time (the first with a throwaway certificate, the second with a
# stand-in for the file its start script writes), so a configuration that does not parse never
# becomes an image. The certificate is removed again; the real one is mounted at /etc/nginx/tls.
RUN apk add --no-cache --virtual .check openssl \
    && mkdir -p /etc/nginx/tls \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=build-check \
       -keyout /etc/nginx/tls/privkey.pem -out /etc/nginx/tls/fullchain.pem 2>/dev/null \
    && nginx -t \
    && printf '%s\n' 'set_real_ip_from 192.0.2.1;' 'real_ip_header CF-Connecting-IP;' \
       'geo $realip_remote_addr $qd_from_tunnel { default 0; 192.0.2.1 1; }' > /tmp/qd-tunnel-peer.conf \
    && nginx -t -c /etc/nginx/nginx.single-host.conf \
    && rm -rf /etc/nginx/tls/* /tmp/* \
    && apk del .check
# Unprivileged: nginx listens on 8080 and 8443 inside the container and writes only under /tmp.
USER 101:101
EXPOSE 8080 8443
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/nginx-health"]
