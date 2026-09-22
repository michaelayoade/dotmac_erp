"""The migration credential must fail before deployment side effects."""

from pathlib import Path


DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


def test_existing_migration_credential_is_checked_before_backup_and_pull() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    early = deploy.index(
        'echo "-> Preflight: existing migration executor credential..."'
    )
    backup = deploy.index('echo "→ Backing up database')
    pull = deploy.index("git pull --rebase")
    candidate = deploy.index('echo "→ Preflight: migration executor contract..."')

    assert early < backup < pull < candidate
    early_block = deploy[early:backup]
    assert "-e MIGRATION_DATABASE_URL app" in early_block
    assert "python scripts/bootstrap_database_roles.py --verify-only" in early_block
    assert "exit 1" in early_block
