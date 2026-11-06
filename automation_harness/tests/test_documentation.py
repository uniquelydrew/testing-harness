from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "automation_harness" / "README.md",
    *(ROOT / "docs").glob("*.md"),
    ROOT / "java-agent" / "README.md",
    ROOT / "javafx_agent" / "README.md",
)
LINK = re.compile(r"(?<!!)\[[^]]*\]\(([^)]+)\)")


def test_documentation_internal_links_resolve_from_each_document():
    missing = []
    for document in DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK.findall(text):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "data:")):
                continue
            if not (document.parent / target).resolve().exists():
                missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    assert not missing, "broken internal Markdown links:\n" + "\n".join(missing)


def test_documented_authoring_launch_and_project_schema_match_current_interfaces():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bundled = (ROOT / "automation_harness" / "README.md").read_text(encoding="utf-8")

    for text in (readme, bundled):
        assert "automation-author --project" not in text
        assert "automation-author --repository" not in text
        assert "version: 2\nname: Login workflow\ntest_plans:" in text
        assert "step_registries:" in text
        assert "object_repositories:" in text


def test_script_step_and_entrypoint_docs_cover_the_current_registration_path():
    script_steps = (ROOT / "docs" / "script-backed-steps.md").read_text(encoding="utf-8")
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "--script-step ./script_steps/environment_prepare.ahstep" in script_steps
    assert "do not register script-step" in script_steps
    for command in re.findall(r'"(automation-[a-z0-9-]+)=', setup):
        assert command in readme
