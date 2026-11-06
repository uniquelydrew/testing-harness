from setuptools import find_packages, setup

setup(
    name="automation-harness",
    version="0.5.2",
    description="Local-first automation harness with explicit execution backends and semantic object identity",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.12,<3.13",
    packages=find_packages(include=("automation_harness", "automation_harness.*")),
    # Package data is declared explicitly below.  Do not let setuptools infer
    # namespace-like example/resource directories from the source tree.
    include_package_data=False,
    package_data={
        "automation_harness": [
            "resources/**/*.yaml",
            "resources/**/*.json",
            "examples/**/*.yaml",
            "examples/**/*.py",
            "examples/**/*.png",
        ]
    },
    install_requires=[
        "PyYAML>=6.0.1,<7.0",
        "typing_extensions>=4.12,<5.0",
    ],
    extras_require={
        "pytest": ["pytest>=8.0,<9.0"],
        "vision": ["Pillow>=10.4,<11.0"],
        "test": [
            "Pillow>=10.4,<11.0",
            "PyGObject==3.44.2; platform_system == 'Linux'",
            "pycairo==1.26.1; platform_system == 'Linux'",
            "pytest>=8.0,<9.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "automation-run=automation_harness.compat.live_cli:run_cli",
            "automation-plan=automation_harness.runner.plan_cli:run",
            "automation-reference=automation_harness.reference.app:main",
            "automation-author=automation_harness.authoring.entrypoint:main",
            "automation-javafx=automation_harness.javafx_cli:main",
            "automation-java-target=automation_harness.compat.java_target:main",
            "automation-codex-runtime=automation_harness.codex_runtime:main",
            "automation-pytest=automation_harness.compat.pytest_cli:main",
        ]
    },
)
