from __future__ import annotations

from dataclasses import replace

import pytest

from proofaudit.config import load_config, write_config
from proofaudit.models import ConfigError, ItemSpec


BASE_PROOF = """# First
First argument.
# Second
Second argument.
# Third
Third argument.
"""


def test_item_id_cannot_escape_report_directory(tmp_path, make_project):
    config = make_project(tmp_path / "audit", BASE_PROOF)
    unsafe = replace(
        config,
        items=(
            ItemSpec("../outside", "Unsafe", "lines", 1, 2),
            *config.items[1:],
        ),
    )
    write_config(unsafe)

    with pytest.raises(ConfigError, match="invalid item id"):
        load_config(config.root)


def test_source_must_remain_inside_project(tmp_path, make_project):
    config = make_project(tmp_path / "audit", BASE_PROOF)
    outside = tmp_path / "outside.md"
    outside.write_text("Outside", encoding="utf-8")
    unsafe = replace(config, source="../outside.md")
    write_config(unsafe)

    with pytest.raises(ConfigError, match="must stay inside"):
        load_config(config.root)


def test_decimal_item_ids_are_supported(tmp_path, make_project):
    config = make_project(tmp_path / "audit", BASE_PROOF)
    decimal = replace(
        config,
        items=(
            ItemSpec("5.13", "Decimal label", "lines", 1, 2),
            *config.items[1:],
        ),
    )
    write_config(decimal)

    assert load_config(config.root).items[0].id == "5.13"

