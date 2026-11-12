#!/bin/sh
# Container entrypoint for `smeltery bench`.
#
# The container binds 0.0.0.0, and Bench refuses a non-loopback bind without a token (docs/bench.md, Security). So:
# use SMELTERY_BENCH_TOKEN when the operator set one; otherwise generate a random one for this start. Bench itself
# prints "open http://localhost:PORT/?token=... to sign this browser in" on stderr, which is the sign-in URL
# (`docker logs <container>`). A generated token changes on every start, so set SMELTERY_BENCH_TOKEN to keep one.
set -eu

if [ -z "${SMELTERY_BENCH_TOKEN:-}" ]; then
    SMELTERY_BENCH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
    export SMELTERY_BENCH_TOKEN
    echo "smeltery bench: SMELTERY_BENCH_TOKEN was not set; generated one for this start (see the sign-in URL below)" >&2
fi

exec smeltery bench "$@"
