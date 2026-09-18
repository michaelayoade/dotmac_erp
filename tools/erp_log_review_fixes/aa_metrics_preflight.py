"""Apply the metrics patch with explicit safe subprocess test boundaries."""

from pathlib import Path
import runpy

_recipe = runpy.run_path(str(Path(__file__).with_name("metrics_preflight.py")))
BRANCH = _recipe["BRANCH"]
TITLE = _recipe["TITLE"]
TESTS = _recipe["TESTS"]


def apply(change, write, rewrite):
    _recipe["apply"](change, write, rewrite)
    path = "tests/test_metrics_remote_write_preflight.py"

    def trusted_cli(source):
        return source.replace(
            "subprocess.run(",
            "subprocess.run(  # noqa: S603 - repository-owned synthetic test harness\n",
        ).replace('["bash", str(script)]', '["/bin/bash", str(script)]')

    rewrite(path, "test_cli_never_echoes_sensitive_configuration", trusted_cli)
    rewrite(path, "test_deployment_refuses_placeholder_before_migration_or_runtime_changes", trusted_cli)
