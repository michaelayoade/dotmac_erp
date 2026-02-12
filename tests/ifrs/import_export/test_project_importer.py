from __future__ import annotations

import pytest

from app.services.pm.import_export import ProjectImporter


def test_project_importer_missing_code(import_config, mock_db):
    importer = ProjectImporter(mock_db, import_config)
    with pytest.raises(ValueError, match="Project Code is required"):
        importer.create_entity({"project_name": "Upgrade"})
