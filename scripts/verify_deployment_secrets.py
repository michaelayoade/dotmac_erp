"""Names-only candidate boot-material receipt; run via python -m scripts.verify_deployment_secrets."""

from app.services.deployment_secret_readiness import (
    MaterialStatus,
    inspect_boot_material,
    serialize_candidate_result,
)


def main() -> int:
    result = inspect_boot_material()
    print(serialize_candidate_result(result))
    return 0 if result.status == MaterialStatus.READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
