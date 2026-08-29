from __future__ import annotations

import argparse
import json
import sys

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


def _parser():
    parser = argparse.ArgumentParser(
        prog="automation-javafx",
        description="Inspect and qualify Automation Harness JavaFX bridge endpoints.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="List live instrumented JavaFX JVMs and windows")

    tree = subparsers.add_parser("tree", help="Dump the JavaFX scene graph from live bridge endpoints")
    tree.add_argument("--depth", type=int, default=8, help="Maximum Node tree depth (default: 8)")

    capture = subparsers.add_parser("capture", help="Wait for and print the next JavaFX click")
    capture.add_argument("--timeout", type=float, default=30.0, help="Capture timeout in seconds")

    point = subparsers.add_parser("point", help="Inspect a JavaFX Node at desktop coordinates")
    point.add_argument("x", type=int)
    point.add_argument("y", type=int)

    return parser


def _status(driver):
    endpoints = driver.endpoints()
    payload = {"endpoint_count": len(endpoints), "endpoints": []}
    for endpoint in endpoints:
        item = {
            "pid": endpoint.pid,
            "host": endpoint.host,
            "port": endpoint.port,
            "java_version": endpoint.java_version,
            "command": endpoint.command,
            "discovery_file": str(endpoint.discovery_file),
        }
        try:
            response = endpoint.request("windows", timeout=2.0)
            item["windows"] = response.get("windows", [])
        except Exception as exc:
            item["error"] = "%s: %s" % (type(exc).__name__, exc)
        payload["endpoints"].append(item)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if endpoints else 1


def _tree(driver, depth):
    endpoints = driver.endpoints()
    if not endpoints:
        print("No live JavaFX bridge endpoints discovered.", file=sys.stderr)
        return 1
    payload = []
    for endpoint in endpoints:
        response = endpoint.request("tree", timeout=5.0, max_depth=max(0, depth))
        payload.append({
            "pid": endpoint.pid,
            "command": endpoint.command,
            "windows": response.get("windows", []),
        })
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _capture(driver, timeout):
    captured = driver.capture_next_click(timeout=timeout)
    print(json.dumps(captured.to_dict(), indent=2, sort_keys=True, default=str))
    return 0


def _point(driver, x, y):
    captured = driver.capture_at_point(x, y)
    print(json.dumps(captured.to_dict(), indent=2, sort_keys=True, default=str))
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    driver = JavaFxBridgeDriver()
    if args.command == "status":
        return _status(driver)
    if args.command == "tree":
        return _tree(driver, args.depth)
    if args.command == "capture":
        return _capture(driver, args.timeout)
    if args.command == "point":
        return _point(driver, args.x, args.y)
    raise AssertionError("unhandled command: %s" % args.command)


if __name__ == "__main__":
    sys.exit(main())
