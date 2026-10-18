# syntax=docker/dockerfile:1
FROM python:3.14-alpine
RUN apk add --no-cache openssh-client
# answers ssh-add's passphrase prompt once; gives up on "Bad passphrase" retries
COPY --chmod=755 <<'EOF' /usr/local/bin/askpass
#!/bin/sh
case "$1" in Bad*) exit 1;; esac
printf '%s\n' "$BM_PASS"
EOF
WORKDIR /app
COPY server.py collect.py index.html ./
CMD ["ssh-agent", "python3", "-u", "server.py"]
