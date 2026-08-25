from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_nginx_static_sync_includes_packaged_ui_assets() -> None:
    script = (REPO_ROOT / "scripts" / "sync-static.sh").read_text(encoding="utf-8")

    assert "from app.ui import UI_ASSET_DIRECTORY, UI_ASSET_MOUNT" in script
    assert "docker cp \\" in script
    assert '"$APP_CONTAINER:$UI_ASSET_SOURCE/."' in script
    assert 'rsync -a --delete "$STAGING_DIR/" "$DEST"' in script
    assert 'rsync -a --delete "$SRC" "$DEST"' not in script
