import ast
import inspect

from .base_pass import BasePass
from ..common import a2s, parse_expr, make_name
from ..transforms import ContextualTransforms


class FindCall(ast.NodeTransformer):
    def __init__(self, inliner, globls):
        self.call_obj = None
        self.call_expr = None
        self.inliner = inliner
        self.globls = globls
        self.ret_var = None

    def visit_Call(self, call_expr):
        # for example, self.foo(1)
        try:
            # get the runtime object for function self.foo
            call_obj = eval(a2s(call_expr.func), self.globls, self.globls)
        except Exception:
            return call_expr

        if self.inliner.should_inline(call_obj):
            # TODO: eliminate this condition
            if self.call_expr is not None:
                print(a2s(call_expr).strip())
                raise Exception("Multiple valid call expr")

            self.call_expr = call_expr
            self.call_obj = call_obj

            # Heuristically make a good name for the drop-in variable
            func = call_expr.func
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            else:
                func_name = 'func'

            self.ret_var = self.inliner.fresh(f'{func_name}_ret')
            return make_name(self.ret_var)

        self.generic_visit(call_expr)
        return call_expr

    # Classes using @property have accessors that are actually calling
    # functions. This visitor looks for uses of @property.
    def visit_Attribute(self, attr):
        # for example, foo.x where the class of foo has @property def x()
        try:
            # get the runtime object for foo
            prop_obj = eval(a2s(attr.value), self.globls, self.globls)
        except Exception:
            # if we can't find it, ignore
            return attr

        # if foo should be inlined, and it is an instance of a class,
        # and the class has the attribute, and the attribute is a property
        if self.inliner.should_inline(prop_obj) and \
           hasattr(prop_obj, '__class__') and \
           hasattr(prop_obj.__class__, attr.attr):
            prop = getattr(prop_obj.__class__, attr.attr)
            if isinstance(prop, property):
                # foo.x is same as Foo.x.fget(foo), so we treat the property
                # as a function call so we can pass it to the function inliner
                self.call_obj = prop.fget
                self.call_expr = parse_expr("{}.{}_getter({})".format(
                    prop_obj.__class__.__name__, attr.attr, a2s(attr.value)))
                self.ret_var = self.inliner.fresh(attr.attr)
                return make_name(self.ret_var)

        self.generic_visit(attr)
        return attr


class FindComprehension(ast.NodeTransformer):
    def __init__(self, find_call, ret_var):
        self.find_call = find_call
        self.ret_var = ret_var
        self.comp = None

    def visit_ListComp(self, comp):
        self.find_call.visit(comp)
        if self.find_call.call_expr is not None:
            self.comp = comp
            return make_name(self.ret_var)
        else:
            return comp


class FindIfExp(ast.NodeTransformer):
    def __init__(self, inliner):
        self.inliner = inliner
        self.ifexp = None
        self.ret_var = None
        self.assign_name = None

    def visit_Assign(self, stmt):
        if len(stmt.targets) == 1 and \
           isinstance(stmt.targets[0], ast.Name):
            self.assign_name = stmt.targets[0].id
            self.generic_visit(stmt)
            self.assign_name = None
        else:
            self.generic_visit(stmt)
        return stmt

    def visit_IfExp(self, ifexp):
        self.ifexp = ifexp
        name = self.assign_name + '_ifexp' if self.assign_name is not None else 'ifexp'
        self.ret_var = self.inliner.fresh(name)
        return make_name(self.ret_var)


class FindAssignment(ast.NodeVisitor):
    def __init__(self, var):
        self.assgn = None
        self.var = var

    def visit_Assign(self, stmt):
        if len(stmt.targets) == 1 and \
           isinstance(stmt.targets[0], ast.Name) and \
           stmt.targets[0].id == self.var:
            self.assgn = stmt.value


class InlinePass(BasePass):
    """
    Primary inlining functionality. Looks for inline-able syntax objects
    and inlines them. Currently inlines:
    - Functions
    - Objects (constructors and methods)
    - Generators
    - With blocks
    - List comprehensions
    - If-expressions

    Basic strategy is to start at a statement, e.g. an assignment, then
    search the assignment for inlineable objects. If one is found, it is
    replaced by a fresh variable, and the logic to assign that variable
    is placed above the statement.

    Example:
      def foo():
        return 1
      assert foo() + 1 == 2

      >> becomes (roughly) >>

      foo_ret = 1
      assert foo_ret + 1 == 2

    Most of the code in this module just finds the relevant objects. The
    code to perform the inlining lives in the ContextualTransforms class
    in transforms.py.
    """
    tracer_args = {}

    def __init__(self, inliner):
        super().__init__(inliner)
        self.fns = ContextualTransforms(self.inliner, self.globls)

    def visit_For(self, stmt):
        self.generic_visit(stmt)

        mod = ast.Module([ast.Expr(stmt.iter)])
        self.visit(mod)
        stmt.iter = mod.body[-1].value

        return mod.body[:-1] + [stmt]

    def visit_With(self, stmt):
        self.generic_visit(stmt)

        context = eval(a2s(stmt.items[0].context_expr), self.globls,
                       self.globls)
        if self.inliner.should_inline(context):
            self.change = True
            return self.fns.expand_with(stmt)
        else:
            return [stmt]

    def _visit_import(self, imprt):
        if len(imprt.names) == 1:
            alias = imprt.names[0]
            source_name = alias.name
            imported_name = alias.asname or alias.name
            try:
                obj = eval(imported_name, self.globls, self.globls)
            except Exception:
                return imprt

            if self.inliner.should_inline(obj) and \
               not inspect.ismodule(obj) and \
               not inspect.isclass(obj):

                if isinstance(imprt, ast.ImportFrom):
                    assert imprt.level == 0
                    exec(f'import {imprt.module}', self.globls, self.globls)
                    mod_obj = eval(imprt.module, self.globls, self.globls)
                else:
                    assert False, "TODO"

                mod_ast = ast.parse(open(inspect.getsourcefile(mod_obj)).read())
                finder = FindAssignment(source_name)
                finder.visit(mod_ast)

                # TODO: generate for finder.assgn
                if finder.assgn is not None:
                    return ast.Assign(targets=[make_name(imported_name)],
                                      value=finder.assgn)

        return imprt

    def generic_visit(self, node):
        if isinstance(node, (ast.Assert, ast.Assign, ast.Expr)):
            return self._inline(node)

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._visit_import(node)

        return super().generic_visit(node)

    def _inline(self, stmt):
        ifexp_finder = FindIfExp(self.inliner)
        ifexp_finder.visit(stmt)
        if ifexp_finder.ifexp is not None:
            self.change = True
            return self.fns.expand_ifexp(ifexp_finder.ifexp,
                                         ifexp_finder.ret_var) + [stmt]

        comp_ret_var = self.inliner.fresh('comp')
        comp_call_finder = FindCall(self.inliner, self.globls)
        comp_finder = FindComprehension(comp_call_finder, comp_ret_var)
        comp_finder.visit(stmt)
        if comp_finder.comp is not None:
            self.change = True
            return self.fns.expand_comprehension(comp_finder.comp, comp_ret_var,
                                                 comp_call_finder) + [stmt]

        call_finder = FindCall(self.inliner, self.globls)
        call_finder.visit(stmt)

        new_stmts = []
        if call_finder.call_expr is not None:
            ret_var = call_finder.ret_var
            call_expr = call_finder.call_expr
            call_obj = call_finder.call_obj

            if inspect.ismethod(call_obj):
                imprt = self.fns.expand_method(call_obj, call_expr, ret_var)
                if imprt is not None:
                    new_stmts.insert(0, imprt)
                new_stmts.append(
                    ast.Assign(targets=[make_name(ret_var)], value=call_expr))

            elif inspect.isgeneratorfunction(call_obj):
                new_stmts.extend(
                    self.fns.inline_generator_function(call_obj, call_expr,
                                                       ret_var))

            elif inspect.isfunction(call_obj):
                new_stmts.extend(
                    self.fns.inline_function(call_obj, call_expr, ret_var))

            elif inspect.isclass(call_obj):
                new_stmts.extend(
                    self.fns.inline_constructor(call_obj, call_expr, ret_var))

            elif hasattr(call_obj, '__call__'):
                self.fns.expand_callable(call_expr)
                new_stmts.append(
                    ast.Assign(targets=[make_name(ret_var)], value=call_expr))

            else:
                print(call_obj, type(call_obj), a2s(call_expr).strip())
                raise NotYetImplemented

            self.change = True

        new_stmts.append(stmt)
        return new_stmts