"""Run only on hosted CI or the dedicated Docker test server, never locally.

Usage: python tests/native/test_erp_credential_proof_projection.py --image <cached-tag>
Fixtures are never started; Docker's real Go formatter renders the helper literal.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid

TEST_IMAGE: str | None = None
HELPER = Path(__file__).resolve().parents[2] / "scripts/erp_credential_proof_server.py"
OWNER_LABEL = "io.dotmac.native.erp-proof-projection"
BOUNDED_RUN = None
OVERRIDE_KEYS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_SYNTHETIC_FIXTURE",
    "GCONV_PATH",
    "GLIBC_TUNABLES",
)


def docker(*arguments: str) -> str:
    assert BOUNDED_RUN is not None
    command = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *arguments]
    code, output = BOUNDED_RUN(command, b"", timeout=15, limit=8192)
    if code:
        raise subprocess.CalledProcessError(code, command)
    return output.decode().strip()


class NativeCredentialProofProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global BOUNDED_RUN
        if TEST_IMAGE is None:
            raise unittest.SkipTest("native Docker lane requires explicit --image")
        # Reuse the owner's bounded stdout, discarded stderr and fixed environment.
        BOUNDED_RUN = runpy.run_path(str(HELPER))["_bounded_run"]
        cls.run_id = uuid.uuid4().hex
        cls.image_id = docker("image", "inspect", "--format", "{{.Id}}", TEST_IMAGE)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", cls.image_id):
            raise ValueError("cached fixture image identity")
        if json.loads(
            docker(
                "image", "inspect", "--format", "{{json .Config.Volumes}}", cls.image_id
            )
        ):
            raise ValueError("fixture image must not create inherited volumes")
        sys.stderr.write(f"Native fixture run={cls.run_id} image={cls.image_id}\n")
        tree = ast.parse(HELPER.read_text())
        cls.projection = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "container_projection"
                for target in node.targets
            )
        )

    def cleanup_fixture(self, name: str):
        # The name/label are registered before create: recover a lost CLI response
        # without removing anything except this run's exact fixture and image.
        found = docker(
            "container",
            "ls",
            "--all",
            "--filter",
            f"name=^/{name}$",
            "--filter",
            f"label={OWNER_LABEL}={self.run_id}",
            "--format",
            "{{.ID}}",
        )
        if not found:
            return
        self.assertEqual(len(found.splitlines()), 1)
        projection = (
            "[{{json .Id}},{{json .Name}},{{json .Image}},"
            '{{json (index .Config.Labels "'
            + OWNER_LABEL
            + '")}},{{json .State.Running}}]'
        )
        identity = json.loads(
            docker("inspect", "--type", "container", "--format", projection, name)
        )
        self.assertEqual(identity[1:], ["/" + name, self.image_id, self.run_id, False])
        self.assertRegex(identity[0], r"^[0-9a-f]{64}$")
        docker("rm", identity[0])

    def fixture(self, override: str | None, mounts: bool):
        name = "erp-proof-projection-" + uuid.uuid4().hex
        command = [
            "create",
            "--pull=never",
            "--network",
            "none",
            "--name",
            name,
            "--label",
            f"{OWNER_LABEL}={self.run_id}",
            "--workdir",
            "/synthetic-workdir",
            "--entrypoint",
            "/bin/true",
        ]
        for key in OVERRIDE_KEYS:
            command.extend(["--env", f"{key}={'synthetic' if key == override else ''}"])
        command.extend(["--env", "BENIGN_PROJECTION_FIXTURE=synthetic"])
        destinations = []
        if mounts:
            directory = tempfile.TemporaryDirectory(prefix="erp-proof-projection-")
            self.addCleanup(directory.cleanup)
            for index, destination in enumerate(
                ("/synthetic/plain", "/synthetic/space destination")
            ):
                source = Path(directory.name) / str(index)
                source.mkdir()
                command.extend(
                    ["--mount", f"type=bind,src={source},dst={destination},readonly"]
                )
                destinations.append(destination)
        self.addCleanup(self.cleanup_fixture, name)
        container_id = docker(*command, self.image_id)
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError("created fixture container identity")
        return name, container_id, destinations

    def test_actual_docker_projection(self):
        for override in (None, *OVERRIDE_KEYS):
            for mounts in (False, True):
                with self.subTest(override=override, mounts=mounts):
                    name, container_id, destinations = self.fixture(override, mounts)
                    rows = json.loads(
                        docker(
                            "inspect",
                            "--type",
                            "container",
                            "--format",
                            self.projection,
                            container_id,
                        )
                    )
                    self.assertIsInstance(rows, list)
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual(
                        set(row), {"Id", "Name", "State", "Image", "Config", "Mounts"}
                    )
                    self.assertEqual(row["Id"], container_id)
                    self.assertEqual(row["Name"], "/" + name)
                    self.assertEqual(row["State"], {"Running": False})
                    self.assertEqual(row["Image"], self.image_id)
                    self.assertEqual(
                        row["Config"],
                        {
                            "Image": self.image_id,
                            "WorkingDir": "/synthetic-workdir",
                            "ProcessOverride": override is not None,
                        },
                    )
                    self.assertIs(
                        row["Config"]["ProcessOverride"], override is not None
                    )
                    self.assertEqual(
                        sorted(item["Destination"] for item in row["Mounts"]),
                        sorted(destinations),
                    )
                    for mount in row["Mounts"]:
                        self.assertEqual(set(mount), {"Destination"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Already cached public fixture image/tag; never pulled",
    )
    TEST_IMAGE = parser.parse_args().image
    # Hard outer bound; run ownership labels permit exact reconciliation after
    # termination. The coordinator/CI owns this disposable Docker test daemon.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(120)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
