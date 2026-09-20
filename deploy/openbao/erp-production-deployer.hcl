# The production deployment controller is a consumer of the canonical
# migration credential. It cannot list the mount, read sibling secrets, or
# create/update/delete any secret.
path "secret/data/dotmac/postgres/erp-shared-primary/app_admin" {
  capabilities = ["read"]
}
