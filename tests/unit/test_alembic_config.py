"""Unit tests for Alembic configuration.

Validates that alembic config loads correctly and that Base.metadata
reflects all 11 expected domain tables — no live database required.
"""

import os
import sys

# Ensure src/ is on the path (mirrors what alembic/env.py does)
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

EXPECTED_TABLES = frozenset(
    [
        "people",
        "templates",
        "template_tasks",
        "template_task_assignees",
        "template_dependencies",
        "projects",
        "tasks",
        "assignments",
        "dependencies",
        "day_overrides",
        "plan_versions",
        "audit_log",
        "task_history",
        # capability layer
        "roles",
        "skills",
        "role_skills",
        "person_roles",
    ]
)


# ---------------------------------------------------------------------------
# Alembic config loading
# ---------------------------------------------------------------------------


def test_alembic_config_loads():
    """alembic.ini can be parsed and the script_location is set."""
    from alembic.config import Config

    ini_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "alembic.ini"
    )
    cfg = Config(ini_path)
    script_location = cfg.get_main_option("script_location")
    assert script_location is not None
    assert "alembic" in script_location


def test_alembic_sqlalchemy_url_is_set():
    """alembic.ini contains a non-default sqlalchemy.url."""
    from alembic.config import Config

    ini_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "alembic.ini"
    )
    cfg = Config(ini_path)
    url = cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    assert url != "driver://user:pass@localhost/dbname"
    assert "planner" in url


# ---------------------------------------------------------------------------
# Base.metadata table coverage
# ---------------------------------------------------------------------------


def test_base_metadata_has_all_11_tables():
    """Base.metadata must contain exactly the 11 domain tables."""
    import planner.infra.db.models  # noqa: F401 — registers all models
    from planner.infra.db.base import Base

    actual = set(Base.metadata.tables.keys())
    assert actual == EXPECTED_TABLES, (
        f"Missing: {EXPECTED_TABLES - actual}, Extra: {actual - EXPECTED_TABLES}"
    )


def test_base_metadata_table_count():
    """Sanity-check: 17 tables (13 domain/audit + 4 capability)."""
    import planner.infra.db.models  # noqa: F401
    from planner.infra.db.base import Base

    assert len(Base.metadata.tables) == 17


# ---------------------------------------------------------------------------
# Migration file existence and revision chain
# ---------------------------------------------------------------------------


def test_initial_migration_file_exists():
    """The 0001_initial.py migration file is present in alembic/versions/."""
    versions_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "alembic", "versions"
    )
    files = os.listdir(versions_dir)
    migration_files = [f for f in files if "0001" in f and f.endswith(".py")]
    assert migration_files, "No file matching '0001*.py' found in alembic/versions/"


def test_initial_migration_revision_id():
    """The initial migration module declares revision = '0001' and down_revision = None."""
    import importlib.util

    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "alembic",
        "versions",
        "0001_initial.py",
    )
    spec = importlib.util.spec_from_file_location("migration_0001", migration_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    assert module.revision == "0001"
    assert module.down_revision is None


def test_initial_migration_has_upgrade_and_downgrade():
    """The initial migration exposes both upgrade() and downgrade() callables."""
    import importlib.util

    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "alembic",
        "versions",
        "0001_initial.py",
    )
    spec = importlib.util.spec_from_file_location("migration_0001_fns", migration_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    assert callable(getattr(module, "upgrade", None)), "upgrade() not found"
    assert callable(getattr(module, "downgrade", None)), "downgrade() not found"
