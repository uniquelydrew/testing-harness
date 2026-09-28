from setuptools import find_packages, setup

setup(
    name="automation-harness",
    version="0.5.2",
    description="Local-first automation harness with explicit execution backends and semantic object identity",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.6,<3.7",
    packages=find_packages(include=("automation_harness", "automation_harness.*")),
    include_package_data=True,
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
        "PyYAML>=3.12,<6.0",
        "dataclasses==0.8; python_version < '3.7'",
        "typing_extensions==4.1.1",
    ],
    extras_require={
        "vision": ["Pillow==8.4.0"],
        "test": [
            "Pillow==8.4.0",
            "PyGObject==3.38.0; platform_system == 'Linux'",
            "pycairo==1.20.1; platform_system == 'Linux'",
            "pytest>=4.5,<7.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "automation-run=automation_harness.compat.live_cli:run_cli",
            "automation-plan=automation_harness.runner.plan_cli:run",
            "automation-reference=automation_harness.compat.python36:run_reference",
            "automation-author=automation_harness.authoring.entrypoint:main",
            "automation-javafx=automation_harness.compat.python36:run_javafx",
            "automation-java-target=automation_harness.compat.java_target:main",
        ]
    },
)
