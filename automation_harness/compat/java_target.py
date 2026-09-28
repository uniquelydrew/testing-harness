"""Scoped launcher for target JVM instrumentation.

This intentionally modifies JAVA_TOOL_OPTIONS only for the launched process
tree. It never changes the calling shell or the harness itself.
"""
from __future__ import print_function

import os
import shlex
import sys


_AGENT_ENV = {
    "swing": "AUTOMATION_HARNESS_JAVA_AGENT",
    "javafx": "AUTOMATION_HARNESS_JAVAFX_AGENT",
}


def child_environment(agent, environ=None):
    env = dict(os.environ if environ is None else environ)
    if agent not in _AGENT_ENV:
        raise ValueError("agent must be one of: %s" % ", ".join(sorted(_AGENT_ENV)))
    jar = env.get(_AGENT_ENV[agent], "").strip()
    if not jar:
        raise ValueError(
            "%s is not set; source .automation-harness-env after bootstrap"
            % _AGENT_ENV[agent]
        )
    if not os.path.isfile(jar):
        raise ValueError("configured %s agent JAR does not exist: %s" % (agent, jar))

    inherited = env.get("JAVA_TOOL_OPTIONS", "").strip()
    harness_tokens = []
    retained = []
    for token in _split_java_tool_options(inherited):
        if token.startswith("-javaagent:") and "automation-harness" in token:
            harness_tokens.append(token)
        else:
            retained.append(token)
    if harness_tokens:
        raise ValueError(
            "JAVA_TOOL_OPTIONS already contains an Automation Harness javaagent; "
            "remove global agent injection before using automation-java-target"
        )

    retained.append("-javaagent:%s" % jar)
    env["JAVA_TOOL_OPTIONS"] = " ".join(_quote_java_tool_option(item) for item in retained)
    env["AUTOMATION_HARNESS_SCOPED_AGENT"] = agent
    return env


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    agent = None
    if len(args) >= 2 and args[0] == "--agent":
        agent = args[1].strip().casefold()
        args = args[2:]
    elif args and args[0] in ("--swing", "--javafx"):
        agent = args[0][2:]
        args = args[1:]

    if args and args[0] == "--":
        args = args[1:]

    if agent is None or not args:
        print(
            "usage: automation-java-target --agent {swing|javafx} -- <target-command> [args...]",
            file=sys.stderr,
        )
        return 2

    try:
        env = child_environment(agent)
    except ValueError as exc:
        print("automation-java-target: %s" % exc, file=sys.stderr)
        return 2

    os.execvpe(args[0], args, env)
    return 127


def _split_java_tool_options(value):
    if not value:
        return []
    try:
        return shlex.split(value)
    except ValueError:
        # JAVA_TOOL_OPTIONS is normally shell-like text. If a malformed legacy
        # value is present, preserve it as one token rather than silently
        # rewriting the user's JVM options.
        return [value]


def _quote_java_tool_option(value):
    # JAVA_TOOL_OPTIONS is tokenized by the JVM, not by a shell. Quoting paths
    # with spaces keeps the agent argument intact on supported HotSpot builds.
    if not any(character.isspace() for character in value):
        return value
    return '"%s"' % value.replace('"', '\\"')


if __name__ == "__main__":
    sys.exit(main())
