from pathlib import Path

import pytest

from steel_crm.datagen.generator import generate_export
from steel_crm.ingest.dataverse import load_export
from steel_crm.warehouse.star_schema import build_warehouse


@pytest.fixture(scope="session")
def export_dir(tmp_path_factory) -> Path:
    """A smaller export (one year) so the suite runs in seconds."""
    out = tmp_path_factory.mktemp("dynamics_export")
    generate_export(out, seed=7, start="2025-01-01", end="2025-12-31")
    return out


@pytest.fixture(scope="session")
def export(export_dir):
    return load_export(export_dir)


@pytest.fixture(scope="session")
def warehouse(export):
    return build_warehouse(export)
