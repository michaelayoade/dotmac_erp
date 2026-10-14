#!/bin/sh
# Root-owned 0755 at /usr/local/libexec/dotmac-erp-proof-launcher.
# SSH_ORIGINAL_COMMAND is compared as a string and is never evaluated.
if [ "$#" -ne 0 ] || [ "${SSH_ORIGINAL_COMMAND-}" != 'dotmac-erp-credential-proof-v1' ]; then
    printf '%s\n' '{"stage":"runtime","status":"refused"}'
    exit 2
fi
exec /usr/bin/sudo -n -- /usr/local/libexec/dotmac-erp-credential-proof.py
