"""Command-line packaging and inspection for .ahext extensions."""
from __future__ import annotations

import argparse
import json

from .package import ExtensionPackage, build_extension
from .registry import ExtensionRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="automation-extension", description="Build and inspect .ahext packages")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build from a source directory")
    build.add_argument("source")
    build.add_argument("output")
    verify = commands.add_parser("verify", help="Verify archive and dependency compatibility")
    verify.add_argument("packages", nargs="+")
    inspect = commands.add_parser("inspect", help="Inspect archive metadata, without executing adapters")
    inspect.add_argument("package")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            package = build_extension(args.source, args.output)
            print("Built %s@%s -> %s" % (package.extension_id, package.version, package.path))
        elif args.command == "verify":
            registry = ExtensionRegistry()
            for source in args.packages:
                registry.register(source)
            for name in registry.dependency_order():
                print("Verified %s@%s" % (name, next(p.version for p in registry.packages if p.extension_id == name)))
        else:
            package = ExtensionPackage.open(args.package)
            print(json.dumps(package.manifest, indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, "%s: %s\n" % (type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
