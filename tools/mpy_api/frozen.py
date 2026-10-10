"""Inventory manifest-selected Python source without executing frozen modules."""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import keyword
import math
from pathlib import PurePosixPath
import tokenize
import unicodedata
from typing import Any


_UNSUPPORTED = {"kind": "unresolved", "reason": "source value contract is not resolved"}


def _identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name) and unicodedata.normalize("NFKC", name) == name


def _expression_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_name(node.value)
        if parent:
            return parent + "." + node.attr
    return None


def _literal(node: ast.AST | None) -> dict:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return {"kind": "None", "value": None}
        for kind, cls in (("bool", bool), ("int", int), ("float", float),
                          ("complex", complex), ("str", str), ("bytes", bytes)):
            if type(value) is cls:
                # Keep the normalized inventory JSON serializable.
                if cls in (bytes, complex):
                    return {"kind": kind, "literal": repr(value)}
                if cls is float and not math.isfinite(value):
                    return {"kind": kind, "literal": ast.unparse(node),
                            "value_resolution": {"status": "unresolved", "reason": "nonfinite number retained as a source expression, not a JSON number"}}
                return {"kind": kind, "value": value}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        if value.get("kind") in ("int", "float"):
            if "value" not in value:
                return {**value, "literal": ast.unparse(node)}
            return {"kind": value["kind"], "value": (-1 if isinstance(node.op, ast.USub) else 1) * value["value"]}
    if isinstance(node, ast.Tuple):
        return {"kind": "tuple", "elements": [_literal(item) for item in node.elts]}
    if isinstance(node, (ast.List, ast.Set)):
        return {"kind": "list" if isinstance(node, ast.List) else "set"}
    if isinstance(node, ast.Dict):
        return {"kind": "dict"}
    return dict(_UNSUPPORTED)


def _scope_nodes(body: list[ast.stmt]):
    """Traverse one lexical scope, excluding bodies of nested definitions."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _execution_expressions(node: ast.AST, depth: int = 0):
    """Yield potentially eager expressions, excluding deferred function bodies.

    Depth separates a class body's locals from its containing namespace.
    """
    expressions = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions = list(node.decorator_list) + list(node.args.defaults) + [value for value in node.args.kw_defaults if value is not None]
    elif isinstance(node, ast.ClassDef):
        expressions = list(node.decorator_list) + list(node.bases) + [item.value for item in node.keywords]
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        expressions = ([node.value] if node.value is not None else []) + (list(node.targets) if isinstance(node, ast.Assign) else [node.target])
    elif isinstance(node, (ast.If, ast.While)):
        expressions = [node.test]
    elif isinstance(node, ast.Assert):
        expressions = [node.test] + ([node.msg] if node.msg is not None else [])
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        expressions = [node.iter, node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        expressions = [part for item in node.items for part in (item.context_expr, item.optional_vars) if part is not None]
    elif isinstance(node, ast.Try):
        expressions = [handler.type for handler in node.handlers if handler.type is not None]
    elif isinstance(node, ast.Delete):
        expressions = list(node.targets)
    elif isinstance(node, ast.Expr):
        expressions = [node.value]
    for expression in expressions:
        yield expression, depth
    if isinstance(node, ast.ClassDef):
        for statement in _class_execution_statements(node.body):
            yield from _execution_expressions(statement, depth + 1)


def _class_execution_statements(body: list[ast.stmt]):
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant) and type(node.test.value) in (bool, int, str, type(None)):
            yield from _class_execution_statements(node.body if node.test.value else node.orelse)
        else:
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    yield from _class_execution_statements(nested)
            for handler in getattr(node, "handlers", []):
                yield from _class_execution_statements(handler.body)


class _Collector:
    def __init__(self, record: dict, physical: str, module_name: str, tree: ast.Module):
        self.record = record
        self.physical = physical
        self.module_name = module_name
        self.tree = tree
        self.identity = f"frozen:{record['root']}/{record['path']}->{record['target_path']}"
        self.gaps: list[dict] = []
        self.dynamic_providers: list[dict] = []
        self.shadowed = self.bound_names(tree.body)
        self.declared_global_names = {name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names}
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        eager_callees = set()
        for statement in _class_execution_statements(tree.body):
            for expression, depth in _execution_expressions(statement):
                for item in _scope_nodes([expression]):
                    if depth != 0 or not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name) or item.func.id not in {"globals", "locals"}:
                        continue
                    parent = parents.get(item)
                    grandparent = parents.get(parent)
                    discarded = isinstance(parent, ast.Expr) and parent.value is item
                    direct_target = isinstance(parent, ast.Subscript) and parent.value is item and isinstance(parent.ctx, (ast.Store, ast.Del))
                    immediate_method = isinstance(parent, ast.Attribute) and parent.value is item and isinstance(grandparent, ast.Call) and grandparent.func is parent
                    if discarded or direct_target or immediate_method:
                        eager_callees.add(item.func)
        # Deferred namespace helpers, escaped maps/bound methods, and generated
        # exec/eval code can affect names bound after the originating access.
        self.namespace_escape = any(isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and
                                    item.id in {"globals", "locals", "exec", "eval"} and item not in eager_callees
                                    for item in ast.walk(tree))

    def source(self, node: ast.AST) -> dict:
        return {"path": self.physical, "root": self.record["root"], "relative_path": self.record["path"],
                "start_line": getattr(node, "lineno", 1),
                "end_line": (max((getattr(item, "end_lineno", 1) or 1 for item in ast.walk(node)), default=1)
                             if isinstance(node, ast.Module) else getattr(node, "end_lineno", 1)),
                "start_column": getattr(node, "col_offset", 0), "end_column": getattr(node, "end_col_offset", 0)}

    def resolution(self, status: str, node: ast.AST, reason: str | None = None) -> dict:
        result = {"status": status, "source": self.source(node), "authority": "selected Python source AST"}
        if reason:
            result["reason"] = reason
        return result

    def gap(self, node: ast.AST, reason: str, *, dynamic: bool = False) -> None:
        gap = {"reason": reason, "construct": type(node).__name__, "source": self.source(node)}
        self.gaps.append(gap)
        if dynamic:
            self.dynamic_providers.append(gap)

    def symbol(self, name: str, kind: str, node: ast.AST, prefix: str) -> dict:
        return {"name": name, "kind": kind,
                "identity": f"{self.identity}::{prefix}{name}@{getattr(node, 'lineno', 1)}:{getattr(node, 'col_offset', 0)}",
                "source": self.source(node),
                "resolution": {"availability": self.resolution("selected-source", node)}}

    @staticmethod
    def bound_names(body: list[ast.stmt]) -> set[str]:
        names: set[str] = set()
        for node in _scope_nodes(body):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names if alias.name != "*")
        return names

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str, *, in_class: bool,
                 class_shadowed: set[str]) -> dict:
        symbol = self.symbol(node.name, "function", node, prefix)
        descriptor = "function"
        decorators = [_expression_name(item) for item in node.decorator_list]
        unsupported = []
        if len(decorators) > 1:
            unsupported.append("composed decorators are unassessed")
        for decorator in decorators:
            if in_class and decorator in ("staticmethod", "classmethod") and decorator not in self.shadowed and decorator not in class_shadowed:
                descriptor = decorator
            else:
                unsupported.append(decorator or "non-name decorator expression")
        args = node.args
        positional = args.posonlyargs + args.args
        defaults: list[ast.AST | None] = [None] * (len(positional) - len(args.defaults)) + args.defaults
        offset = 1 if in_class and descriptor != "staticmethod" else 0
        if offset and not positional:
            unsupported.append("bound method has no positional receiver")
            offset = 0
        parameters = []
        for index, (argument, default) in enumerate(zip(positional, defaults)):
            if index < offset:
                continue
            parameters.append(self.parameter(argument, default, "positional_only" if index < len(args.posonlyargs) else "positional_or_keyword"))
        if args.vararg:
            parameters.append(self.parameter(args.vararg, None, "var_positional"))
        parameters.extend(self.parameter(argument, default, "keyword_only")
                          for argument, default in zip(args.kwonlyargs, args.kw_defaults))
        if args.kwarg:
            parameters.append(self.parameter(args.kwarg, None, "var_keyword"))
        symbol["callshape"] = {
            "minimum": sum(default is None for default in defaults),
            "maximum": None if args.vararg else len(positional), "keywords": bool(args.args or args.kwonlyargs or args.kwarg),
            "implicit_argument_offset": offset, "descriptor": descriptor,
            "receiver_name": positional[0].arg if offset else None,
            "receiver_kind": "positional_only" if offset and args.posonlyargs else "positional_or_keyword",
            "parameters": parameters, "parameters_resolution": "resolved", "function": node.name,
        }
        symbol["resolution"]["callshape"] = self.resolution("source-resolved", node)
        symbol["semantics"] = self.returns(node, parameters)
        symbol["resolution"]["returns"] = self.resolution(symbol["semantics"]["return_resolution"], node,
                                                            "; ".join(symbol["semantics"].get("return_unresolved", [])) or None)
        symbol["resolution"]["inputs"] = self.resolution("unresolved", node, "accepted input domains are not established by Python annotations")
        if node.returns:
            symbol["annotation_candidate"] = ast.unparse(node.returns)
        if unsupported:
            symbol["kind"] = "unknown"
            symbol["source_callshape_candidate"] = copy.deepcopy(symbol["callshape"])
            symbol["callshape"] = {"descriptor": "decorated-unassessed", "parameters_resolution": "unresolved"}
            symbol["resolution"]["callshape"] = self.resolution("unresolved", node, "decorator/native wrapper callshape is unassessed")
            symbol["semantics"]["return_resolution"] = "unresolved"
            symbol["semantics"]["return_unresolved"].append("decorator/native result transformation is unassessed")
            symbol["resolution"]["returns"] = self.resolution("unresolved", node, "decorator/native result transformation is unassessed")
            symbol["resolution"]["decorators"] = self.resolution("unresolved", node, "decorator rebinding semantics are unassessed")
            symbol["unresolved"] = ["unsupported decorators: " + ", ".join(unsupported)]
            self.gap(node, symbol["unresolved"][0])
        return symbol

    def parameter(self, argument: ast.arg, default: ast.AST | None, kind: str) -> dict:
        result = {"name": argument.arg, "kind": kind, "required": default is None,
                  "source": self.source(argument), "resolution": self.resolution("source-resolved", argument),
                  "type_resolution": "unresolved", "type_contract": dict(_UNSUPPORTED)}
        if default is not None:
            result["default_contract"] = _literal(default)
            result["default_contract"]["source"] = self.source(default)
            result["default_contract"]["resolution"] = self.resolution(
                "source-resolved" if result["default_contract"]["kind"] != "unresolved" else "unresolved", default)
        if argument.annotation:
            result["annotation_candidate"] = ast.unparse(argument.annotation)
        return result

    def returns(self, node: ast.FunctionDef | ast.AsyncFunctionDef, parameters: list[dict]) -> dict:
        body = node.body
        nodes = list(_scope_nodes(body))
        reasons = []
        if isinstance(node, ast.AsyncFunctionDef):
            reasons.append("async result protocol is unassessed")
        if any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in nodes):
            reasons.append("generator result protocol is unassessed")
        flow = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
        if any(isinstance(item, flow) for item in nodes):
            reasons.append("complete successful return paths/control flow are unassessed")
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for item in nodes):
            reasons.append("nested-scope writes/rebinding are unassessed")
        if any(isinstance(item, ast.Call) and _expression_name(item.func) in ("exec", "eval", "locals") for item in nodes):
            reasons.append("dynamic local execution/rebinding is unassessed")
        names = {parameter["name"] for parameter in parameters if parameter["kind"] not in ("var_positional", "var_keyword")}
        written = {item.id for item in nodes if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del))}
        contracts = []
        terminated = False
        for statement in body:
            if terminated:
                break
            if isinstance(statement, ast.Return):
                if isinstance(statement.value, ast.Name) and statement.value.id in names and statement.value.id not in written:
                    contract = {"kind": "argument", "exported_argument": statement.value.id,
                                "authority": "unchanged Python parameter on a straight-line successful return"}
                else:
                    contract = _literal(statement.value) if statement.value is not None else {"kind": "None", "value": None}
                contract["source"] = self.source(statement)
                contracts.append(contract)
                terminated = True
            elif isinstance(statement, ast.Raise):
                terminated = True
        if not terminated:
            contracts.append({"kind": "None", "value": None, "source": self.source(node),
                              "authority": "Python successful fallthrough"})
        if not contracts:
            reasons.append("no successful result contract is established")
        if any(contract["kind"] == "unresolved" or any(e["kind"] == "unresolved" for e in contract.get("elements", [])) for contract in contracts):
            reasons.append("returned expression is not a resolved literal or unchanged parameter")
        return {"inputs": [], "input_resolution": "unresolved",
                "input_unresolved": ["accepted input domains are not established by Python annotations"],
                "returns": contracts, "return_resolution": "unresolved" if reasons else "source-resolved",
                "return_unresolved": sorted(set(reasons))}

    def class_definition(self, node: ast.ClassDef, prefix: str) -> dict:
        symbol = self.symbol(node.name, "class", node, prefix)
        bases = [_expression_name(base) for base in node.bases]
        symbol["bases"] = [base for base in bases if base]
        symbol["resolution"]["bases"] = self.resolution("unresolved" if any(not base for base in bases) else "source-resolved", node,
                                                        "base expression is not a qualified name" if any(not base for base in bases) else None)
        gap_start = len(self.gaps)
        dynamic_start = len(self.dynamic_providers)
        class_shadowed = self.bound_names(node.body)
        members = self.statements(node.body, prefix + node.name + ".", in_class=True, class_shadowed=class_shadowed)
        # Explicit instance writes are source names, but their execution and types
        # need not follow from the static class dictionary.
        known = {member["name"] for member in members}
        for statement in node.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) or not statement.args.args:
                continue
            receiver = statement.args.args[0].arg
            if any(_expression_name(item) in ("staticmethod", "classmethod") for item in statement.decorator_list):
                continue
            for item in _scope_nodes(statement.body):
                if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store) and isinstance(item.value, ast.Name) and item.value.id == receiver and item.attr not in known:
                    member = self.symbol(item.attr, "constant", item, prefix + node.name + ".")
                    member["value_contract"] = dict(_UNSUPPORTED)
                    member["resolution"]["availability"] = self.resolution("conditional-unassessed", item, "instance write execution is not established")
                    member["binding"] = "instance"
                    members.append(member)
                    known.add(item.attr)
        symbol["members"] = members
        symbol["constructor"] = {"resolution": self.resolution("unresolved", node, "target class creation/constructor inheritance is unassessed"),
                                 "source": self.source(node)}
        symbol["slots"] = []
        if "__slots__" in known:
            slot_member = next(member for member in members if member["name"] == "__slots__")
            symbol["slots"].append({"kind": "source-__slots__", "value_contract": slot_member.get("value_contract"),
                                    "source": slot_member["source"], "resolution": "unassessed-target-slot-creation"})
            self.gap(node, "target __slots__ descriptor creation/availability is unassessed")
        policy = {"kind": "source-class-with-framework-gap", "closed": False,
                  "framework": {"status": "unassessed", "reason": "target base/type attribute lookup is not established by Python AST"},
                  "unresolved": [gap["reason"] for gap in self.gaps[gap_start:]]}
        providers = self.dynamic_providers[dynamic_start:]
        if "__getattr__" in known or "__getattribute__" in known:
            providers = providers + [{"kind": "source-attribute-provider", "source": self.source(node)}]
        if providers:
            policy.update({"kind": "dynamic-provider", "dynamic_providers": providers})
        symbol["attribute_policy"] = policy
        # Class-level providers must not open the containing module.
        del self.dynamic_providers[dynamic_start:]
        if node.decorator_list or node.keywords:
            symbol["kind"] = "unknown"
            symbol["resolution"]["class_creation"] = self.resolution("unresolved", node, "class decorators/metaclass keywords are unassessed")
            self.gap(node, "class decorators/metaclass keywords are unassessed")
        return symbol

    def assignments(self, node: ast.Assign | ast.AnnAssign | ast.AugAssign, prefix: str) -> list[dict]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        result = []
        for target in targets:
            if isinstance(target, ast.Name):
                symbol = self.symbol(target.id, "constant", target, prefix)
                symbol["value_contract"] = _literal(value) if not isinstance(node, ast.AugAssign) else dict(_UNSUPPORTED)
                symbol["resolution"]["value"] = self.resolution(
                    "source-resolved" if symbol["value_contract"]["kind"] != "unresolved" else "unresolved", node)
                if isinstance(node, ast.AnnAssign):
                    symbol["annotation_candidate"] = ast.unparse(node.annotation)
                    if value is None:
                        symbol["resolution"]["availability"] = self.resolution("conditional-unassessed", node, "annotation-only assignment need not bind a runtime value")
                if isinstance(value, ast.Name) and not isinstance(node, ast.AugAssign):
                    symbol["alias_candidate"] = value.id
                result.append(symbol)
            else:
                self.gap(node, "destructuring or non-name assignment effects are unassessed")
                for item in ast.walk(target):
                    if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                        symbol = self.symbol(item.id, "unknown", item, prefix)
                        symbol["resolution"]["value"] = self.resolution("unresolved", node, "destructuring assignment is unassessed")
                        result.append(symbol)
        return result

    @staticmethod
    def available(symbol: dict) -> bool:
        return _status_of(symbol.get("resolution", {}).get("availability", {})) in ("selected-source", "source-resolved", "resolved")

    def join_namespaces(self, branches: list[list[dict]], node: ast.AST, prefix: str) -> dict[str, dict]:
        namespaces = [{symbol["name"]: symbol for symbol in branch} for branch in branches]
        result = {}
        for name in sorted(set().union(*(namespace.keys() for namespace in namespaces))):
            variants = [namespace[name] for namespace in namespaces if name in namespace]
            if len(variants) == len(namespaces) and all(variant == variants[0] for variant in variants[1:]):
                result[name] = copy.deepcopy(variants[0])
                continue
            symbol = self.symbol(name, "unknown", node, prefix)
            present = len(variants) == len(namespaces) and all(self.available(variant) for variant in variants)
            symbol["resolution"]["availability"] = self.resolution(
                "selected-source" if present else "conditional-unassessed", node,
                "name is bound on every assessed branch" if present else "name may be absent on an execution branch")
            symbol["resolution"]["value"] = self.resolution("unresolved", node, "branch-dependent value identity is unassessed")
            symbol["value_contract"] = dict(_UNSUPPORTED)
            symbol["binding_alternatives"] = copy.deepcopy(variants)
            result[name] = symbol
        return result

    def uncertain_effects(self, body: list[ast.stmt], initial: dict[str, dict], node: ast.AST, prefix: str, *, in_class: bool = False) -> dict[str, dict]:
        symbols = copy.deepcopy(initial)
        nodes = list(_scope_nodes(body))
        bound = self.bound_names(body)
        deleted = {item.id for item in nodes if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Del)}
        parts = [(expression, depth) for statement in _class_execution_statements(body)
                 for expression, depth in _execution_expressions(statement)]
        executed = [item for expression, _depth in parts for item in _scope_nodes([expression])]
        dynamic = any(
            isinstance(item, ast.Call) and _expression_name(item.func) in
            ({"exec", "eval", "locals"} if in_class and depth == 0 else
             {"exec", "eval", "globals", "locals"} if not in_class and depth == 0 else
             {"exec", "eval", "globals"} if not in_class else set())
            for expression, depth in parts for item in _scope_nodes([expression]))
        if dynamic:
            self.gap(node, "current namespace mutation/exposure is unassessed", dynamic=True)
        may_call = any(isinstance(item, (ast.Call, ast.Attribute, ast.Subscript, ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)) for item in executed)
        if not in_class and may_call:
            deleted.update(self.declared_global_names)
        if self.namespace_escape and may_call:
            deleted.update(symbols)
        affected = bound | deleted | (set(symbols) if dynamic else set())
        for name in sorted(affected):
            previous = symbols.get(name)
            symbol = self.symbol(name, "unknown", node, prefix)
            present = previous is not None and self.available(previous) and name not in deleted and not dynamic
            symbol["resolution"]["availability"] = self.resolution(
                "selected-source" if present else "conditional-unassessed", node,
                "an existing name may be rebound but no deletion is selected" if present else "unsupported effects may bind or delete this name")
            symbol["resolution"]["value"] = self.resolution("unresolved", node, "execution-dependent value is unassessed")
            symbol["value_contract"] = dict(_UNSUPPORTED)
            if previous:
                symbol["binding_alternatives"] = [copy.deepcopy(previous)]
            symbols[name] = symbol
        return symbols

    def expression_effects(self, node: ast.AST, symbols: dict[str, dict], prefix: str, *, in_class: bool, target_only: bool = False) -> dict[str, dict]:
        writes: set[str] = set()
        deletions: set[str] = set()
        dynamic = False
        possible_call = bool(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list)
        if isinstance(node, ast.ClassDef):
            possible_call = True
        if isinstance(node, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            possible_call = True
        parts = list(_execution_expressions(node))
        if target_only:
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            parts = [(target, 0) for target in targets if not isinstance(target, ast.Name)]
            possible_call = False
        for expression, depth in parts:
            nodes = list(_scope_nodes([expression]))
            calls = [item for item in nodes if isinstance(item, ast.Call)]
            possible_call |= bool(calls)
            # Operators and lookup can dispatch to user callbacks. Purity is not
            # established from Python syntax or a callee's spelling.
            possible_call |= any(isinstance(item, (ast.Attribute, ast.Subscript, ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)) for item in nodes)
            if isinstance(node, (ast.If, ast.While, ast.Assert)) and not isinstance(expression, ast.Constant):
                possible_call = True
            for item in nodes:
                if depth == 0 and isinstance(item, ast.NamedExpr) and isinstance(item.target, ast.Name):
                    writes.add(item.target.id)
            namespace_calls = {_expression_name(call.func) for call in calls} & {"exec", "eval", "globals", "locals"}
            if in_class:
                current_namespace = depth == 0 and bool(namespace_calls & {"exec", "eval", "locals"})
            else:
                current_namespace = bool(namespace_calls & ({"exec", "eval", "globals", "locals"} if depth == 0 else {"exec", "eval", "globals"}))
            if current_namespace:
                dynamic = True
                deletions.update(symbols)
        if not in_class and possible_call:
            deletions.update(self.declared_global_names)
        if self.namespace_escape and possible_call:
            deletions.update(symbols)
        if not in_class and isinstance(node, ast.ClassDef):
            pending = [node]
            while pending:
                definition = pending.pop()
                scoped = list(_scope_nodes(definition.body))
                globals_here = {name for item in scoped if isinstance(item, ast.Global) for name in item.names}
                for statement in _class_execution_statements(definition.body):
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if statement.name in globals_here:
                            writes.add(statement.name)
                        if isinstance(statement, ast.ClassDef):
                            pending.append(statement)
                    if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
                        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                        writes.update(item.id for target in targets for item in ast.walk(target)
                                      if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store) and item.id in globals_here)
                    if isinstance(statement, ast.Delete):
                        deletions.update(item.id for target in statement.targets for item in ast.walk(target)
                                         if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Del) and item.id in globals_here)
        affected = writes | deletions
        if affected or dynamic:
            self.gap(node, "executed expression effects on source-declared globals/current namespace are unassessed", dynamic=dynamic)
        for name in sorted(affected):
            previous = symbols.get(name)
            replacement = self.symbol(name, "unknown", node, prefix)
            present = previous is not None and self.available(previous) and name not in deletions
            replacement["resolution"]["availability"] = self.resolution(
                "selected-source" if present else "conditional-unassessed", node,
                "expression may replace this value" if present else "expression/callback may bind or delete this name")
            replacement["resolution"]["value"] = self.resolution("unresolved", node, "executed expression/callback value effects are unassessed")
            replacement["value_contract"] = dict(_UNSUPPORTED)
            if previous:
                replacement["binding_alternatives"] = [previous]
            symbols[name] = replacement
        return symbols

    def statements(self, body: list[ast.stmt], prefix: str = "", *, in_class: bool = False,
                   class_shadowed: set[str] | None = None, initial: dict[str, dict] | None = None) -> list[dict]:
        symbols = copy.deepcopy(initial or {})
        class_shadowed = class_shadowed or set()
        scoped_globals = {name for item in _scope_nodes(body) if isinstance(item, ast.Global) for name in item.names} if in_class else set()
        for node in body:
            symbols = self.expression_effects(node, symbols, prefix, in_class=in_class)
            found = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(self.function(node, prefix, in_class=in_class, class_shadowed=class_shadowed))
            elif isinstance(node, ast.ClassDef):
                found.append(self.class_definition(node, prefix))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                found.extend(self.assignments(node, prefix))
                if isinstance(node, ast.AnnAssign) and node.value is None and isinstance(node.target, ast.Name) and node.target.id in symbols:
                    existing = copy.deepcopy(symbols[node.target.id])
                    existing["annotation_candidate"] = ast.unparse(node.annotation)
                    found = [existing]
                if not isinstance(node, ast.AugAssign) and isinstance(node.value, ast.Name) and node.value.id in symbols:
                    target = symbols[node.value.id]
                    for index, alias in enumerate(found):
                        aliased = copy.deepcopy(target)
                        aliased.update({key: alias[key] for key in ("name", "identity", "source")})
                        aliased["alias_of"] = node.value.id
                        aliased["alias_target_identity"] = target["identity"]
                        if self.available(target):
                            aliased["resolution"]["availability"] = alias["resolution"]["availability"]
                        else:
                            aliased["resolution"]["availability"] = self.resolution("conditional-unassessed", node, "alias target availability is unassessed")
                        found[index] = aliased
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        self.gap(node, "star import contributes unresolved runtime names", dynamic=True)
                        for name, previous in list(symbols.items()):
                            replacement = self.symbol(name, "unknown", node, prefix)
                            replacement["resolution"]["availability"] = copy.deepcopy(previous["resolution"]["availability"])
                            replacement["resolution"]["value"] = self.resolution("unresolved", node, "star import may replace an existing value")
                            replacement["binding_alternatives"] = [previous]
                            symbols[name] = replacement
                        continue
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    symbol = self.symbol(name, "module" if isinstance(node, ast.Import) else "unknown", node, prefix)
                    if isinstance(node, ast.Import):
                        symbol["import_binding"] = {"module": alias.name if alias.asname else alias.name.split(".")[0], "level": 0}
                    else:
                        symbol["import_binding"] = {"module": node.module or "", "level": node.level, "name": alias.name}
                    symbol["resolution"]["imported_value"] = self.resolution("unresolved", node, "imported target API is not inferred from an import statement")
                    found.append(symbol)
            elif isinstance(node, ast.If):
                if isinstance(node.test, ast.Constant) and type(node.test.value) in (bool, int, str, type(None)):
                    branch = self.statements(node.body if node.test.value else node.orelse, prefix,
                                             in_class=in_class, class_shadowed=class_shadowed, initial=symbols)
                    symbols = {symbol["name"]: symbol for symbol in branch}
                else:
                    self.gap(node, "conditional module/class execution is unassessed")
                    branches = [self.statements(branch, prefix, in_class=in_class,
                                                class_shadowed=class_shadowed, initial=symbols)
                                for branch in (node.body, node.orelse)]
                    symbols = self.join_namespaces(branches, node, prefix)
                continue
            elif isinstance(node, ast.Try):
                self.gap(node, "try execution and exception selection are unassessed; source-name branches are joined")
                successful = self.statements(node.body, prefix, in_class=in_class, class_shadowed=class_shadowed, initial=symbols)
                successful = self.statements(node.orelse, prefix, in_class=in_class, class_shadowed=class_shadowed,
                                             initial={symbol["name"]: symbol for symbol in successful})
                branches = [successful]
                exception_initial = self.uncertain_effects(node.body, symbols, node, prefix, in_class=in_class)
                for handler in node.handlers:
                    handler_initial = copy.deepcopy(exception_initial)
                    if handler.name:
                        handler_initial[handler.name] = self.symbol(handler.name, "unknown", handler, prefix)
                    handled = self.statements(handler.body, prefix, in_class=in_class,
                                              class_shadowed=class_shadowed, initial=handler_initial)
                    branches.append([symbol for symbol in handled if symbol["name"] != handler.name])
                symbols = self.join_namespaces(branches, node, prefix)
                final = self.statements(node.finalbody, prefix, in_class=in_class, class_shadowed=class_shadowed, initial=symbols)
                symbols = {symbol["name"]: symbol for symbol in final}
                continue
            elif isinstance(node, ast.Delete):
                targets = list(reversed(node.targets))
                while targets:
                    target = targets.pop()
                    if isinstance(target, ast.Name):
                        symbols.pop(target.id, None)
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        targets.extend(reversed(target.elts))
                    else:
                        self.gap(node, "attribute/subscript deletion effects are unassessed")
            elif isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)) or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            else:
                self.gap(node, "module/class execution effects are unassessed")
                symbols = self.uncertain_effects([node], symbols, node, prefix, in_class=in_class)
                continue
            for symbol in found:
                if symbol["name"] not in scoped_globals:
                    symbols[symbol["name"]] = symbol
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
                symbols = self.expression_effects(node, symbols, prefix, in_class=in_class, target_only=True)
        for symbol in symbols.values():
            symbol.pop("alias_binding", None)
            target_name = symbol.get("alias_of")
            if (target_name in symbols and symbols[target_name].get("identity") == symbol.get("alias_target_identity") and
                    self.available(symbols[target_name])):
                symbol["alias_binding"] = {"target_name": target_name, "status": "source-resolved"}
        return [symbols[name] for name in sorted(symbols) if name not in scoped_globals]


def _status_of(resolution: dict) -> str:
    return resolution.get("status", "unresolved")


def collect_frozen_modules(scope: dict) -> list[dict]:
    """Collect declarations from the shared authority's physical selections.

    Target paths define import identity, never the source location. Repeated
    physical files frozen under distinct target names remain distinct modules.
    Parsing or missing physical inputs are operational errors, not empty modules.
    """
    from mpy_analysis.scope import FROZEN_GROUP, source_path

    if scope.get("schema_version") != 1:
        raise ValueError("frozen collection requires scope schema_version=1")
    records = scope.get("groups", {}).get(FROZEN_GROUP, [])
    modules = []
    snapshots = {}
    for record in sorted(records, key=lambda item: (item["target_path"], item["root"], item["path"])):
        physical = source_path(scope, record)
        if physical.suffix.lower() != ".py":
            raise ValueError(f"frozen selection has no Python source: {physical}")
        target = PurePosixPath(record["target_path"])
        if target.is_absolute() or ".." in target.parts or target.suffix != ".py" or "\\" in record["target_path"]:
            raise ValueError(f"unsafe frozen Python target path: {record['target_path']!r}")
        parts = list(target.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        if not parts or any(not _identifier(part) for part in parts):
            raise ValueError(f"frozen target has no safe import identity: {record['target_path']!r}")
        name = ".".join(parts)
        if physical not in snapshots:
            raw = physical.read_bytes()
            try:
                if raw.startswith(b"\xef\xbb\xbf"):
                    raise ValueError(f"frozen source BOM cannot be projected without changing MicroPython identifiers: {physical}")
                encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                if encoding != "utf-8":
                    raise ValueError(f"frozen source encoding cookie is outside the raw UTF-8 AST subset: {physical}: {encoding}")
                text = raw.decode("utf-8")
                for token in tokenize.generate_tokens(io.StringIO(text).readline):
                    if token.type == tokenize.NAME and unicodedata.normalize("NFKC", token.string) != token.string:
                        raise ValueError(f"frozen identifier would be normalized by Python AST: {physical}:{token.start[0]}: {token.string!r}")
                tree = ast.parse(text, filename=str(physical), feature_version=(3, 10))
                for identifier in ast.walk(tree):
                    if isinstance(identifier, (ast.Name, ast.arg)):
                        original = ast.get_source_segment(text, identifier)
                        spelling = identifier.id if isinstance(identifier, ast.Name) else identifier.arg
                        if original is not None:
                            raw_name = original.split(":", 1)[0].strip() if isinstance(identifier, ast.arg) else original.strip()
                            # Python 3.10 uses whole f-string locations for some
                            # embedded names. JoinedStr is guarded separately.
                            if raw_name.isidentifier() and raw_name != spelling:
                                raise ValueError(f"frozen identifier differs from its AST projection: {physical}:{identifier.lineno}: {original!r}")
                    elif isinstance(identifier, (ast.Attribute, ast.keyword)):
                        original = ast.get_source_segment(text, identifier)
                        if original:
                            for token in tokenize.generate_tokens(io.StringIO(original).readline):
                                if token.type == tokenize.NAME and unicodedata.normalize("NFKC", token.string) != token.string:
                                    raise ValueError(f"frozen identifier would be normalized within an expression: {physical}:{identifier.lineno}: {token.string!r}")
                    elif isinstance(identifier, ast.JoinedStr):
                        original = ast.get_source_segment(text, identifier)
                        if original and unicodedata.normalize("NFKC", original) != original:
                            raise ValueError(f"frozen f-string is outside the NFKC-stable segment projection subset: {physical}:{identifier.lineno}")
            except (SyntaxError, UnicodeError, LookupError, tokenize.TokenError) as exc:
                raise ValueError(f"cannot parse selected frozen source {physical}: {exc}") from exc
            snapshots[physical] = (raw, tree, hashlib.sha256(raw).hexdigest())
        raw, tree, source_digest = snapshots[physical]
        collector = _Collector(record, str(physical), name, tree)
        exports = collector.statements(tree.body)
        names = {symbol["name"] for symbol in exports}
        if "__getattr__" in names:
            collector.dynamic_providers.append({"kind": "source-module-getattr", "source": collector.source(tree),
                                                "reason": "target dispatch support/domain is unassessed"})
        policy = {"kind": "source-module-with-framework-gap", "closed": False,
                  "framework": {"status": "unassessed", "reason": "target module framework attributes are not inferred from frozen AST"},
                  "unresolved": sorted(set(gap["reason"] for gap in collector.gaps))}
        if collector.dynamic_providers:
            policy.update({"kind": "dynamic-provider", "dynamic_providers": collector.dynamic_providers})
        modules.append({"name": name, "identity": collector.identity, "source": collector.source(tree),
                        "exports": exports, "attribute_policy": policy,
                        "resolution": {"availability": collector.resolution("selected-source", tree),
                                       "effects": collector.resolution("unresolved", tree, "effect analysis is bounded to source-declared globals and direct/escaped/deferred namespace effects; arbitrary cross-module/callback effects remain unassessed"),
                                       "coverage": collector.resolution("unresolved" if collector.gaps else "source-resolved", tree,
                                                                         "unsupported execution constructs retained" if collector.gaps else None)},
                        "resolution_gaps": collector.gaps, "is_package": is_package,
                        "effect_regions": {"declared_globals": sorted(collector.declared_global_names),
                                           "escaped_or_deferred_namespace_access": collector.namespace_escape},
                        "provenance": {"kind": "frozen-python-source", "root": record["root"], "path": record["path"],
                                       "source_identity": f"{record['root']}/{record['path']}",
                                       "physical_path": str(physical), "target_path": record["target_path"],
                                       "is_package": is_package, "owner": record["owner"],
                                       "bytes": len(raw), "sha256": source_digest,
                                       "source_frontend": "raw-UTF-8, NFKC-stable identifiers/f-string segments, Python-3.10 AST subset; no bytecode/body equivalence",
                                       "selection": record["provenance"]}})
    for physical, (raw, _tree, source_digest) in snapshots.items():
        current = physical.read_bytes()
        if len(current) != len(raw) or hashlib.sha256(current).hexdigest() != source_digest:
            raise ValueError(f"selected frozen source changed during collection: {physical}")
    return modules
