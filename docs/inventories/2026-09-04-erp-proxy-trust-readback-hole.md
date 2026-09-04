# ERP proxy trust: what is declared, what runs, and what nothing compares

**Dated characterization, 2026-09-04. Facts, not mandates.** Sibling of
`2026-08-30-erp-foundation-03-adoption-preflight.md`, which records the other
`[ingress]` holes against the pinned `dotmac-deployment-foundation`.

Written alongside the `app/net.py` repair that closes the first TWO gaps below:
a malformed `TRUSTED_PROXY_IPS` entry now refuses at import instead of vanishing,
and a bare address is one host in both address families instead of silently
becoming an IPv6 `/32` supernet. The remaining two are recorded as **unmonitored
regions, not exemptions** (ADR-0018), each with a named remediation.

---

## 1. Who reads the client address

Five call sites, all security-relevant, all reached through `app/net.py`:

| consumer | file | reads |
|---|---|---|
| rate limiting | `app/middleware/rate_limit.py:294,372` | `get_client_ip` |
| CSRF | `app/web/csrf.py:66,111` | `is_from_trusted_proxy`, `get_request_scheme` |
| password-reset URL | `app/api/auth_flow.py:361-362` | `get_request_host`, `get_request_scheme` |
| HR API | `app/api/people/hr.py:123-124` | `get_request_host`, `get_request_scheme` |
| employee web | `app/services/people/hr/web/employee_web.py:201-202` | `get_request_host`, `get_request_scheme` |

Plus the audit trail, from the request-context repair onward:
`app/services/audit_listener.py:273` and `app/services/audit_dispatcher.py:104`
both record `ip_address_var`, which `ObservabilityMiddleware` now fills from
`get_client_ip` rather than `request.client.host`.

A wrong answer from `app/net.py` is therefore not one wrong log line. It is
shared rate-limit buckets across every real client behind the proxy, CSRF and
reset-URL reasoning on a lost forwarded input, and an audit trail whose
originator column is the proxy.

---

## 2. GAP-1 — CLOSED. A malformed entry no longer vanishes

`_parse_trusted_proxy_networks` caught `ValueError` and `continue`d. The
direction of that failure is worth stating precisely, because it was mis-stated
once:

* It **failed closed for forwarded-header trust.** The dropped entry trusted
  nobody, so no header was honoured that would not have been honoured anyway.
* It **failed silently for deployment correctness, and destroyed client
  provenance.** The operator believed they had configured a proxy they had not,
  and from that moment the six consumers above read the proxy's address.

`TrustedProxyConfigurationError` now refuses a non-empty unparseable entry at
import, naming the entry. Empty and separator-only configuration stays valid and
trusts nobody: configuring nothing and configuring garbage are different acts.

**Blast direction, deliberately chosen:** the process does not start, the
container health check fails, and `scripts/deploy.sh` rolls back. A refused,
visible, rolled-back deployment is recoverable. A running one that has quietly
lost provenance is not, and nothing reports it.

---

## 3. GAP-2 — CLOSED. A bare address is one host, in both families

The same rule that turns a bare IPv4 address into a `/32` host route appended
`/32` to a bare IPv6 address. Measured 2026-09-04 against the shipped parsing
rule, BEFORE this repair:

| `TRUSTED_PROXY_IPS` entry | parses to | addresses trusted |
|---|---|---|
| `127.0.0.1` | `127.0.0.1/32` | 1 |
| `10.0.0.1` | `10.0.0.1/32` | 1 |
| `172.16.0.0/12` | `172.16.0.0/12` | 1 048 576 |
| `::1` | `::/32` | **79 228 162 514 264 337 593 543 950 336** |
| `2001:db8::1` | `2001:db8::/32` | **79 228 162 514 264 337 593 543 950 336** |

It parsed cleanly, so the GAP-1 refusal never saw it, and `strict=False` masked
the host bits without complaint. `::1` is the most natural thing an operator
would write for a loopback proxy on a dual-stack host — and ERP already
publishes the `app` service on both families: `docker-compose.yml:59` sets
`host_ip: "::1"` beside the `127.0.0.1` publish, and
`2026-08-30-erp-foundation-03-adoption-preflight.md` § 2.2 records that
asymmetry as a hole. The entry was plausible, not hypothetical.

**Unlike GAP-1 this direction was OPEN, not closed** — any peer inside `::/32`
would have had its `X-Forwarded-For` believed. It is the opposite direction from
the defect the GAP-1 repair was written for, and worse.

**Decided and repaired.** A bare address now yields a host route in both
families, consistent with the IPv4 rule and with the function's own documented
intent. Refusing a bare IPv6 outright was rejected: it would reject `::1`, which
is a legitimate and natural entry. The width is DERIVED from the address rather
than appended, so neither `32` nor `128` is written down in the parser and the
two families cannot drift apart again. An explicit IPv6 prefix is still honoured
as written.

| entry | parses to now | addresses trusted |
|---|---|---|
| `::1` | `::1/128` | 1 |
| `2001:db8::1` | `2001:db8::1/128` | 1 |
| `2001:db8::/32` | `2001:db8::/32` | unchanged — an explicit prefix is honoured |

### How it survived a test named for it

`tests/test_net_trusted_proxy.py::test_a_bare_address_is_parsed_as_a_single_host_network`
already existed and already claimed the family-neutral property IN ITS NAME,
while asserting IPv4 only. A reader checking whether bare addresses were handled
found a test with the right name and stopped. **A test whose name is broader
than its cases is worse than no test**, because it terminates the search. The
cases are now as wide as the name, and
`test_a_bare_ipv6_address_cannot_widen_into_a_supernet` states the security
direction separately so it cannot be narrowed again without the name changing.

### Residual, not repaired

`strict=False` is retained for entries that carry an EXPLICIT prefix, which is
the shipped tolerance: `172.16.0.5/12` still masks to `172.16.0.0/12` without
complaint. That is a different shape from the bare case — the operator wrote a
prefix, so they asked for a network — and it is out of scope here. Noted so it
is not mistaken for having been considered and endorsed.

---

## 4. GAP-3 — OPEN. The declared plan does not reach the process, and nothing reads back

Three statements of ERP's trusted proxies exist. Nothing compares them.

| where | value | reaches the app process? |
|---|---|---|
| `deploy/product.toml` `[ingress] trusted_proxies` | `["172.16.0.0/12", "127.0.0.1"]` | no — it is the typed declaration |
| `docker-compose.yml:69` (the LIVE path) | `TRUSTED_PROXY_IPS: 172.16.0.0/12,127.0.0.1` | yes |
| `deploy/rendered/docker-compose.yml` (the rendered path) | **absent** | **no** |
| `deploy/rendered/nginx/erp.dotmac.io.conf:52-53` | `set_real_ip_from 172.16.0.0/12; set_real_ip_from 127.0.0.1;` | n/a — nginx, not the app |

The renderer projects `[ingress].trusted_proxies` into nginx's `set_real_ip_from`
and **not** into the application container's environment. So the rendered compose
— the asset the foundation adoption is moving toward — starts the app with
`TRUSTED_PROXY_IPS` unset, `_TRUSTED_PROXY_NETWORKS` empty, and every client
address recorded as the proxy's. That is exactly the GAP-1 outcome, reached
without any typo, by a plan that reads as correct.

The GAP-1 refusal does **not** catch it: an unset variable is empty, and empty is
valid by design.

**What is missing is a readback**, in the sense of "declared, then confirmed
applied" rather than "declared". A value that was set but never confirmed to have
taken effect is the same failure class as one that was silently dropped.

### What a readback would need, and why one is not written here

* **The declaration already exists and is the one to build against.**
  `ProductDeploymentSpec.v1`'s `[ingress] trusted_proxies`, in
  `deploy/product.toml:447`, parsed by the pinned
  `dotmac-deployment-foundation==0.2.0a2`, which refuses unknown keys. There is
  no second, newer proxy-trust type anywhere in `dotmac_erp` or in any sibling
  Dotmac checkout — a repository-wide search was run to be sure before any work
  was based on one. A readback binds to THIS field.
* **`deploy/` is not in the runtime image.** The `Dockerfile` COPY list is an
  allowlist — `app`, `alembic`, `alembic.ini`, `gunicorn.conf.py`, `locales`,
  `templates`, `static`, `license` and three named `scripts/` files. A running
  container cannot read `deploy/product.toml`, so an in-process readback needs a
  committed in-image projection of the declared value, the shape
  `app/product_assembly.py` and `app/bill_of_materials.py` already use for
  release identity.
* **The shape is already established in this repository.** `app/runtime_admission.py`
  is the precedent: frozen observation dataclasses, a pure violation function
  over one snapshot, a thin `fetch_snapshot` seam in
  `scripts/verify_runtime_admission.py` (which IS in the image), a printed
  transcript of every check run and skipped, and a `VACUOUS_ADMISSION_NOTICE`
  when the considered set is empty so that silence is never mistaken for a pass.
  A proxy-trust readback is that shape with a different snapshot.

Inventing a new typed block to satisfy the requirement would repeat the mistake
this repository already declined to make once —
`2026-08-30-erp-foundation-03-adoption-preflight.md` § 2.2: *"I have deliberately
not written a placeholder TOML block for this. Inventing a shape that Foundation
0.3 then contradicts would be worse than a named hole."*

**Not built yet, deliberately.** The obstacle is not the contract — that exists
— but the in-image projection the second bullet describes. Building the readback
is explicitly deferred rather than attempted; this section records what it needs
so the next attempt starts from the constraint rather than rediscovering it.

---

## 5. GAP-4 — OPEN. The trusted set is read once, at import

`_TRUSTED_PROXY_NETWORKS` is a module-level constant computed from `os.getenv`
when `app.net` is first imported. The trusted set cannot change without
restarting the process, and `monkeypatch.setenv` after import is inert — which is
why `tests/test_net_trusted_proxy.py` patches the parsed constant and says so.

Carried forward unchanged from the parity tests' original finding. A typed
contract that takes the networks as configuration, rather than reading the
environment at import, subsumes this and GAP-3 together.

---

## Summary

| gap | direction of failure | state |
|---|---|---|
| GAP-1 malformed entry dropped | closed for trust, silent for provenance | **repaired** |
| GAP-2 bare IPv6 became a `/32` supernet | **OPEN for trust**, silent | **repaired** |
| GAP-3 declared plan never read back | closed for trust, silent for provenance | open |
| GAP-4 environment read once at import | neither; operational | open |

Two of four remain, and GAP-3 is the sharper one. GAP-1 and GAP-2 both need an
operator to have typed something — a typo, or a bare IPv6 address. **GAP-3 needs
nobody to do anything wrong at all:** the rendered compose omits the variable,
so the app starts with it unset, reaches the same lost-provenance state, and
every artifact involved reads as correct. Neither repair above can catch it,
because unset is empty and empty is valid by design.

Per ADR-0018 the two remaining gaps are recorded here as unmonitored regions,
not as exemptions: no guard claims to cover them.
