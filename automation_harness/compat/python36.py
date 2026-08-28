"""Python 3.6 compatibility loader for the fixed RHEL 8.6 deployment target."""

import ast
import importlib
import importlib.abc
import importlib.machinery
import re
import sys

_PREFIX = "automation_harness."
_EXCLUDED_PREFIX = "automation_harness.compat"
_INSTALLED = False


def _annotation_text(node):
    if node is None:
        return "Any"
    if isinstance(node, ast.Str):
        return node.s
    constant = getattr(ast, "Constant", None)
    if constant is not None and isinstance(node, constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _annotation_text(node.value) + "." + node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_text(node.value) + "[" + _annotation_text(node.slice) + "]"
    index = getattr(ast, "Index", None)
    if index is not None and isinstance(node, index):
        return _annotation_text(node.value)
    if isinstance(node, ast.Tuple):
        body = ", ".join(_annotation_text(item) for item in node.elts)
        if len(node.elts) == 1:
            body += ","
        return body
    if isinstance(node, ast.List):
        return "[" + ", ".join(_annotation_text(item) for item in node.elts) + "]"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_text(node.left) + " | " + _annotation_text(node.right)
    if isinstance(node, ast.NameConstant):
        return repr(node.value)
    if isinstance(node, ast.Ellipsis):
        return "..."
    return "Any"


def _is_subprocess_call(node):
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in ("run", "Popen")
    )


def _is_add_subparsers_call(node):
    return isinstance(node.func, ast.Attribute) and node.func.attr == "add_subparsers"


class _Python36Transformer(ast.NodeTransformer):
    def _convert_arguments(self, arguments):
        args = list(arguments.args) + list(arguments.kwonlyargs)
        vararg = getattr(arguments, "vararg", None)
        kwarg = getattr(arguments, "kwarg", None)
        if vararg is not None:
            args.append(vararg)
        if kwarg is not None:
            args.append(kwarg)
        for argument in args:
            if getattr(argument, "annotation", None) is not None:
                argument.annotation = ast.Str(s=_annotation_text(argument.annotation))

    def visit_FunctionDef(self, node):
        self._convert_arguments(node.args)
        if node.returns is not None:
            node.returns = ast.Str(s=_annotation_text(node.returns))
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._convert_arguments(node.args)
        if node.returns is not None:
            node.returns = ast.Str(s=_annotation_text(node.returns))
        return self.generic_visit(node)

    def visit_AnnAssign(self, node):
        node.annotation = ast.Str(s=_annotation_text(node.annotation))
        return self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module == "contextvars":
            return ast.copy_location(
                ast.ImportFrom(module="automation_harness.compat.contextvars", names=node.names, level=0),
                node,
            )
        if node.module != "typing":
            return node
        extension_names = [name for name in node.names if name.name in ("Protocol", "runtime_checkable")]
        if not extension_names:
            return node
        normal_names = [name for name in node.names if name.name not in ("Protocol", "runtime_checkable")]
        result = []
        if normal_names:
            result.append(ast.copy_location(ast.ImportFrom(module="typing", names=normal_names, level=0), node))
        result.append(ast.copy_location(ast.ImportFrom(module="typing_extensions", names=extension_names, level=0), node))
        return result

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if _is_subprocess_call(node):
            rewritten = []
            capture_output = False
            for keyword in node.keywords:
                if keyword.arg == "text":
                    keyword.arg = "universal_newlines"
                elif keyword.arg == "capture_output":
                    capture_output = not isinstance(keyword.value, ast.NameConstant) or keyword.value.value is not False
                    continue
                rewritten.append(keyword)
            if capture_output:
                names = set(keyword.arg for keyword in rewritten)
                if "stdout" not in names:
                    rewritten.append(
                        ast.keyword(
                            arg="stdout",
                            value=ast.Attribute(
                                value=ast.Name(id="subprocess", ctx=ast.Load()),
                                attr="PIPE",
                                ctx=ast.Load(),
                            ),
                        )
                    )
                if "stderr" not in names:
                    rewritten.append(
                        ast.keyword(
                            arg="stderr",
                            value=ast.Attribute(
                                value=ast.Name(id="subprocess", ctx=ast.Load()),
                                attr="PIPE",
                                ctx=ast.Load(),
                            ),
                        )
                    )
            node.keywords = rewritten
        elif _is_add_subparsers_call(node):
            node.keywords = [keyword for keyword in node.keywords if keyword.arg != "required"]
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "unlink":
            node.keywords = [keyword for keyword in node.keywords if keyword.arg != "missing_ok"]
        return node


class _Python36SourceLoader(importlib.machinery.SourceFileLoader):
    def source_to_code(self, data, path, _optimize=-1):
        source = importlib.util.decode_source(data)
        source = source.replace("from __future__ import annotations\n", "")
        source = re.sub(r"(?m)^\s*/,[ \t]*(?:#.*)?$", "", source)
        tree = ast.parse(source, filename=path)
        tree = _Python36Transformer().visit(tree)
        ast.fix_missing_locations(tree)
        return compile(tree, path, "exec", dont_inherit=True, optimize=_optimize)


class _Python36Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX) or fullname.startswith(_EXCLUDED_PREFIX):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            return spec
        spec.loader = _Python36SourceLoader(fullname, spec.loader.path)
        return spec


def install():
    global _INSTALLED
    if _INSTALLED or sys.version_info >= (3, 7):
        return
    sys.meta_path.insert(0, _Python36Finder())
    _INSTALLED = True


def _run(module_name, function_name, argv=None):
    install()
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    if argv is None:
        return function()
    return function(argv)


def run_cli():
    return _run("automation_harness.runner.cli", "main")


def run_reference():
    return _run("automation_harness.reference.app", "main")


def run_author():
    return _run("automation_harness.authoring.app", "main")


def run_capture():
    return _run("automation_harness.authoring.app", "capture_main")


def run_repository():
    return _run("automation_harness.authoring.app", "repository_main")
