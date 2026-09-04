from pathlib import Path

from automation_harness.formats import PLAN_SUFFIX, PROJECT_SUFFIX, REPOSITORY_SUFFIX, with_artifact_suffix


def test_new_artifact_names_receive_distinct_yaml_backed_suffixes():
    assert with_artifact_suffix(Path("checkout"), PLAN_SUFFIX).name == "checkout.ahplan"
    assert with_artifact_suffix(Path("suite"), PROJECT_SUFFIX).name == "suite.ahproject"
    assert with_artifact_suffix(Path("desktop"), REPOSITORY_SUFFIX).name == "desktop.ahobjects"


def test_generic_yaml_save_name_is_specialized():
    assert with_artifact_suffix(Path("checkout.yaml"), PLAN_SUFFIX).name == "checkout.ahplan"
