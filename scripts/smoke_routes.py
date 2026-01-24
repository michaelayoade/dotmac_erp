#!/usr/bin/env python3
"""Smoke test key web/API routes.

Logs in to get a bearer token, then probes a list of URLs.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_ROUTES = [
    "/health",
    "/login",
    "/admin/login",
    "/dashboard",
    "/finance/dashboard",
    "/people/hr/employees",
    "/finance/ap/suppliers",
    "/finance/ar/customers",
    "/finance/gl/accounts",
    "/finance/reports",
    "/settings",
]

_FISCAL_PERIOD_PLACEHOLDER = "AUTO_FISCAL_PERIOD_ID"


def _request(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except Exception as exc:
        return None, str(exc)


def login(base_url: str, username: str, password: str) -> str | None:
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    status, body = _request(
        f"{base_url}/auth/login",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    if status != 200:
        print(f"Login failed: status={status}, body={body}")
        return None
    data = json.loads(body)
    return data.get("access_token")


def load_routes(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_ROUTES
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.strip().startswith("#")]


def load_routes_from_openapi(
    base_url: str,
    include_api: bool,
    exclude_prefixes: list[str],
    skip_params: bool,
) -> list[str]:
    status, body = _request(f"{base_url}/openapi.json")
    if status != 200:
        raise SystemExit(f"Failed to load openapi.json: status={status}")
    data = json.loads(body)
    routes = []
    for path, methods in data.get("paths", {}).items():
        if skip_params and "{" in path:
            continue
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        if not include_api and path.startswith("/api"):
            continue
        get_op = methods.get("get")
        if not get_op:
            continue
        params = []
        params.extend(methods.get("parameters", []))
        params.extend(get_op.get("parameters", []))
        query_params = {}
        for param in params:
            if param.get("in") != "query" or not param.get("required"):
                continue
            schema = param.get("schema", {})
            if param["name"] == "fiscal_period_id":
                value = _FISCAL_PERIOD_PLACEHOLDER
            elif "enum" in schema and schema["enum"]:
                value = schema["enum"][0]
            else:
                schema_type = schema.get("type")
                schema_format = schema.get("format")
                if schema_format == "uuid":
                    value = "00000000-0000-0000-0000-000000000000"
                elif schema_format == "date":
                    value = "2024-01-01"
                elif schema_format == "date-time":
                    value = "2024-01-01T00:00:00Z"
                elif schema_type == "integer":
                    value = 1
                elif schema_type == "number":
                    value = 1
                elif schema_type == "boolean":
                    value = True
                elif schema_type == "array":
                    item_schema = schema.get("items", {})
                    value = [item_schema.get("enum", ["test"])[0]]
                else:
                    value = "test"
            query_params[param["name"]] = value
        if query_params:
            query = urllib.parse.urlencode(query_params, doseq=True)
            routes.append(f"{path}?{query}")
        else:
            routes.append(path)
    return sorted(set(routes))


def get_default_fiscal_period_id(base_url: str, headers: dict) -> str | None:
    status, body = _request(
        f"{base_url}/api/v1/gl/fiscal-periods?limit=1",
        headers=headers,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    items = data.get("items") or []
    if not items:
        return None
    return items[0].get("fiscal_period_id")


def substitute_fiscal_period_id(routes: list[str], fiscal_period_id: str | None) -> list[str]:
    if not fiscal_period_id:
        return [path for path in routes if _FISCAL_PERIOD_PLACEHOLDER not in path]
    replaced = []
    for path in routes:
        replaced.append(path.replace(_FISCAL_PERIOD_PLACEHOLDER, fiscal_period_id))
    return replaced


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test routes.")
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--routes-file", help="Optional file with one path per line.")
    parser.add_argument("--from-openapi", action="store_true", help="Load GET routes from openapi.json.")
    parser.add_argument("--include-api", action="store_true", help="Include /api* routes when loading from openapi.")
    parser.add_argument("--exclude-prefix", action="append", default=[], help="Exclude routes by prefix.")
    parser.add_argument("--include-params", action="store_true", help="Include parameterized routes like /items/{id}.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if args.from_openapi:
        routes = load_routes_from_openapi(
            base_url,
            include_api=args.include_api,
            exclude_prefixes=args.exclude_prefix,
            skip_params=not args.include_params,
        )
    else:
        routes = load_routes(args.routes_file)
    token = login(base_url, args.username, args.password)

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    fiscal_period_id = get_default_fiscal_period_id(base_url, headers)
    routes = substitute_fiscal_period_id(routes, fiscal_period_id)

    failures = []
    for path in routes:
        status, body = _request(f"{base_url}{path}", headers=headers)
        if status is None or status >= 400:
            failures.append((path, status, body[:200]))
            print(f"FAIL {path} status={status} body={body[:200]!r}")
        else:
            print(f"OK   {path} status={status}")

    if failures:
        print("\nFailures:")
        for path, status, body in failures:
            print(f"- {path}: status={status} body={body!r}")
        return 1
    print("\nAll routes passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
