from setuptools import find_packages, setup

setup(
    name="automation-harness",
    version="0.5.2",
    description="Protected-target-safe automation harness with an isolated synthetic reference backend",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.6,<3.7",
    packages=find_packages(include=("automation_harness", "automation_harness.*")),
    include_package_data=True,
    package_data={
        "automation_harness": [
            "resources/**/*.yaml",
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
        "test": ["pytest>=4.5,<7.0"],
    },
    entry_points={
        "console_scripts": [
            "automation-run=automation_harness.compat.live_cli:run_cli",
            "automation-reference=automation_harness.compat.python36:run_reference",
            "automation-author=automation_harness.compat.live_author:run_author",
            "automation-capture=automation_harness.compat.live_author:run_capture",
            "automation-repository=automation_harness.compat.live_author:run_repository",
            "automation-javafx=automation_harness.compat.python36:run_javafx",
        ]
    },
)
