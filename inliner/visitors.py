import ast
import copy
import inspect

from .common import *


class Rename(ast.NodeTransformer):
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst

    def visit_FunctionDef(self, fdef):
        if fdef.name == self.src:
            fdef.name = self.dst
            self.generic_visit(fdef)
        else:
            args = fdef.args
            arg_names = set()

            for arg in args.args:
                arg_names.add(arg.arg)
            if args.vararg is not None:
                arg_names.add(args.vararg.arg)
            if args.kwarg is not None:
                arg_names.add(args.kwarg.arg)

            if self.src not in arg_names:
                self.generic_visit(fdef)
        return fdef

    def visit_Name(self, name):
        if name.id == self.src:
            name.id = self.dst
        return name


class Replace(ast.NodeTransformer):
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.replaced = False

    def visit_Name(self, name):
        if name.id == self.name:
            self.replaced = True
            return self.value
        else:
            return name


class FindAssignments(ast.NodeVisitor):
    def __init__(self):
        self.names = set()

    def visit_Assign(self, assgn):
        for t in assgn.targets:
            if isinstance(t, ast.Name):
                self.names.add(t.id)


class ReplaceReturn(ast.NodeTransformer):
    def __init__(self, name):
        self.name = name
        self.toplevel = True
        self.found_return = False
        self.if_wrapper = parse_stmt(
            textwrap.dedent("""
        if "{name}" not in locals() and "{name}" not in globals():
            pass
            """.format(name=self.name)))

    def visit_Return(self, stmt):
        if_stmt = copy.deepcopy(self.if_wrapper)
        # A naked return (without a value) will have stmt.value = None
        value = stmt.value if stmt.value is not None else ast.NameConstant(None)
        if_stmt.body[0] = ast.Assign(targets=[make_name(self.name)],
                                     value=value)
        self.found_return = True
        return if_stmt

    def visit_FunctionDef(self, fdef):
        # no recurse to avoid messing up inline functions
        if self.toplevel:
            self.toplevel = False
            self.generic_visit(fdef)
        return fdef

    def generic_visit(self, node):
        for field, old_value in ast.iter_fields(node):
            if isinstance(old_value, list):
                new_values = []
                for i, cur_value in enumerate(old_value):
                    if isinstance(cur_value, ast.AST):
                        value = self.visit(cur_value)

                        stmt_types = (ast.For, ast.If, ast.With,
                                      ast.FunctionDef, ast.Assign, ast.While)
                        if isinstance(node, stmt_types) and self.found_return:
                            new_values.append(value)
                            if i < len(old_value) - 1:
                                if_stmt = copy.deepcopy(self.if_wrapper)
                                if_stmt.body = old_value[i + 1:]
                                new_values.append(if_stmt)
                            break

                        if value is None:
                            continue
                        elif not isinstance(value, ast.AST):
                            new_values.extend(value)
                            continue

                    new_values.append(value)
                old_value[:] = new_values
            elif isinstance(old_value, ast.AST):
                new_node = self.visit(old_value)
                if new_node is None:
                    delattr(node, field)
                else:
                    setattr(node, field, new_node)

        return node


class ReplaceYield(ast.NodeTransformer):
    def __init__(self, ret_var):
        self.ret_var = ret_var

    def visit_Yield(self, expr):
        append = parse_expr(f'{self.ret_var}.append()')
        append.args.append(expr.value)
        return append


class ReplaceSelf(ast.NodeTransformer):
    def __init__(self, cls, globls):
        self.cls = cls
        self.globls = globls

    def visit_Call(self, expr):
        if isinstance(expr.func, ast.Attribute) and \
            isinstance(expr.func.value, ast.Name) and \
            expr.func.value.id == 'self' and \
            hasattr(self.cls, expr.func.attr): # e.g. calling self.model() where model is attr, not method

            expr.func.value = make_name(self.cls.__name__)

            # If the method being called is bound when directly accessing
            # it on the class, it's probably a @classmethod, and we shouldn't
            # add `self` as an argument
            if not inspect.ismethod(getattr(self.cls, expr.func.attr)):
                expr.args.insert(0, make_name('self'))

        return expr


class ReplaceSuper(ast.NodeTransformer):
    def __init__(self, cls):
        self.cls = cls

    def visit_Call(self, call):
        if isinstance(call.func, ast.Attribute) and \
           isinstance(call.func.value, ast.Call) and \
           isinstance(call.func.value.func, ast.Name) and \
           call.func.value.func.id == 'super':
            call.func.value = make_name(self.cls.__name__)
            call.args.insert(0, make_name('self'))
        self.generic_visit(call)
        return call


class UsedGlobals(ast.NodeVisitor):
    def __init__(self, globls):
        self.globls = globls
        self.used = {}

    def visit_Name(self, name):
        if name.id in self.globls:
            self.used[name.id] = self.globls[name.id]


class CollectLineNumbers(ast.NodeVisitor):
    def __init__(self):
        self.linenos = set()

    def generic_visit(self, node):
        if hasattr(node, 'lineno'):
            self.linenos.add(node.lineno)
        super().generic_visit(node)


class CollectImports(ast.NodeVisitor):
    def __init__(self, mod):
        self.imprts = {}
        self.mod = mod

    def visit_Import(self, imprt):
        for alias in imprt.names:
            name = alias.asname if alias.asname is not None else alias.name
            self.imprts[name] = ast.Import(names=[alias])

    def visit_ImportFrom(self, imprt):
        for alias in imprt.names:
            name = alias.asname if alias.asname is not None else alias.name

            if imprt.level > 0:
                parts = self.mod.split('.')
                mod_level = '.'.join(
                    parts[:-imprt.level]) if len(parts) > 1 else parts[0]
                if imprt.module is not None:
                    module = f'{mod_level}.{imprt.module}'
                else:
                    module = mod_level
            else:
                module = imprt.module

            self.imprts[name] = ast.ImportFrom(module=module,
                                               names=[alias],
                                               level=0)


def collect_imports(call_obj):
    import_collector = CollectImports(mod=inspect.getmodule(call_obj).__name__)
    import_collector.visit(
        ast.parse(open(inspect.getsourcefile(call_obj)).read()))
    return import_collector.imprts


class RemoveFunctoolsWraps(ast.NodeTransformer):
    def visit_FunctionDef(self, fdef):
        if len(fdef.decorator_list) == 1:
            dec = fdef.decorator_list[0]
            if isinstance(dec, ast.Call) and compare_ast(
                    dec.func, parse_expr("functools.wraps")):
                fdef.decorator_list = []
        return fdef


class CollectModules(ast.NodeVisitor):
    def __init__(self, globls):
        self.globls = globls
        self.modules = {}

    def generic_visit(self, node):
        if isinstance(node, (ast.Name, ast.Attribute)):
            try:
                src = a2s(node)
                obj = eval(src, self.globls, self.globls)
            except Exception:
                obj = None

            if obj is not None:
                mod = inspect.getmodule(obj)
                if mod is not None:
                    name = mod.__name__
                    if name not in self.modules:
                        self.modules[name] = src
                return

        super().generic_visit(node)
