from types import NoneType
from typing import Any, Optional, cast

from sgl import TypeReflection

from kernelfunctions.shapes import TShapeOrTuple
from kernelfunctions.typeregistry import get_or_create_type

from .enums import PrimType, IOType
from .pythonvariable import PythonVariable
from .slangvariable import SlangFunction, SlangVariable
from .codegen import CodeGen
from .native import AccessType, CallMode, Shape
from .basetype import BaseType, BindContext


class BoundVariableException(Exception):
    def __init__(self, message: str, variable: 'BoundVariable') -> NoneType:
        super().__init__(message)
        self.message = message
        self.variable = variable


class BoundCall:
    def __init__(self) -> NoneType:
        super().__init__()
        self.args: list['BoundVariable'] = []
        self.kwargs: dict[str, 'BoundVariable'] = {}

    def values(self) -> list['BoundVariable']:
        return self.args + list(self.kwargs.values())

    def apply_implicit_vectorization(self, context: BindContext):
        for arg in self.args:
            arg.apply_implicit_vectorization(context)

        for arg in self.kwargs.values():
            arg.apply_implicit_vectorization(context)

    def finalize_mappings(self, context: BindContext):
        for arg in self.args:
            arg.finalize_mappings(context)

        for arg in self.kwargs.values():
            arg.finalize_mappings(context)


class BoundVariable:
    """
    Node in a built signature tree, maintains a pairing of python+slang marshall,
    and a potential set of child nodes
    """

    def __init__(self, python: PythonVariable, slang: SlangVariable,
                 output_transforms: Optional[dict[str, TShapeOrTuple]] = None,
                 path: Optional[str] = None):

        super().__init__()

        # Store the python and slang marshall
        self.python = python
        self.slang = slang

        # Initialize path
        if path is None:
            self.path = self.slang.name
        else:
            self.path = f"{path}.{self.slang.name}"

        # Get the python marshall for the value + load some basic info
        self.variable_name = self.slang.name

        # Init default properties
        self.access = (AccessType.none, AccessType.none)
        self.differentiable = False
        self.call_dimensionality = None

        # Create children if python value has children
        self.children: Optional[dict[str, BoundVariable]] = None
        if python.fields is not None:
            assert slang.fields is not None
            self.children = {}
            for name, child_python in python.fields.items():
                child_slang = slang.fields[name]
                self.children[name] = BoundVariable(
                    cast(PythonVariable, child_python),
                    cast(SlangVariable, child_slang),
                    output_transforms, self.path)

    @property
    def param_index(self):
        return self.python.parameter_index

    @property
    def vector_mapping(self):
        return self.python.vector_mapping

    @property
    def vector_type(self):
        return self.python.vector_type

    def apply_implicit_vectorization(self, context: BindContext):
        """
        Apply implicit vectorization to this variable. This inspects
        the slang type being bound to in an attempt to get a concrete
        type to provide to the specialization system.
        """
        if self.children is not None:
            for child in self.children.values():
                child.apply_implicit_vectorization(context)
        self._apply_implicit_vectorization(context)

    def _apply_implicit_vectorization(self, context: BindContext):
        if self.python.vector_mapping.valid:
            # if we have a valid vector mapping, just need to reduce it
            self.python.vector_type = self.python.primal.reduce_type(
                len(self.python.vector_mapping))

        if self.python.vector_type is not None:
            # do nothing in first phase if already have a type. vector
            # mapping will be worked out once specialized slang function is known
            pass
        elif self.path == '_result':
            # result is inferred last
            pass
        else:
            # neither specified, attempt to resolve type
            self.python.vector_type = self.python.primal.resolve_type(
                context, self.slang.primal)

        # If we ended up with no valid type, use slang type. Currently this should
        # only happen for auto-allocated result buffers
        if not self.python.vector_mapping.valid and self.python.vector_type is None:
            assert self.path == '_result'
            self.python.vector_type = self.slang.primal

        # Can now calculate dimensionality
        if self.python.vector_mapping.valid:
            if len(self.python.vector_mapping) > 0:
                self.call_dimensionality = max(
                    self.python.vector_mapping.as_tuple())+1
            else:
                self.call_dimensionality = 0
        else:
            assert self.python.vector_type is not None
            self.call_dimensionality = self.python.primal.resolve_dimensionality(
                context, self.python.vector_type)

    def finalize_mappings(self, context: BindContext):
        """
        Finalize vector mappings and types for this variable and children.
        """
        if self.children is not None:
            for child in self.children.values():
                child.finalize_mappings(context)
        self._finalize_mappings(context)

    def _finalize_mappings(self, context: BindContext):
        if self.call_dimensionality is None:
            self.call_dimensionality = context.call_dimensionality

        if not self.python.vector_mapping.valid:
            m: list[int] = []
            for i in range(self.call_dimensionality):
                m.append(context.call_dimensionality - i - 1)
            m.reverse()
            self.python.vector_mapping = Shape(*m)

    def calculate_differentiability(self, context: BindContext):
        """
        Recursively calculate  differentiability
        """

        # Can now decide if differentiable
        self.differentiable = not self.slang.no_diff and self.slang.derivative is not None and self.python.differentiable
        self._calculate_differentiability(context.call_mode)

        if self.children is not None:
            for child in self.children.values():
                child.calculate_differentiability(context)

    def get_input_list(self, args: list['BoundVariable']):
        """
        Recursively populate flat list of argument nodes
        """
        self._get_input_list_recurse(args)
        return args

    def _get_input_list_recurse(self, args: list['BoundVariable']):
        """
        Internal recursive function to populate flat list of argument nodes
        """
        if self.children is not None:
            for child in self.children.values():
                child._get_input_list_recurse(args)
        else:
            args.append(self)

    def __repr__(self):
        return self.python.__repr__()

    def _calculate_differentiability(self, mode: CallMode):
        """
        Calculates access types based on differentiability, call mode and io type
        """
        if mode == CallMode.prim:
            if self.differentiable:
                if self.slang.io_type == IOType.inout:
                    self.access = (AccessType.readwrite, AccessType.none)
                elif self.slang.io_type == IOType.out:
                    self.access = (AccessType.write, AccessType.none)
                else:
                    self.access = (AccessType.read, AccessType.none)
            else:
                if self.slang.io_type == IOType.inout:
                    self.access = (AccessType.readwrite, AccessType.none)
                elif self.slang.io_type == IOType.out:
                    self.access = (AccessType.write, AccessType.none)
                else:
                    self.access = (AccessType.read, AccessType.none)
        elif mode == CallMode.bwds:
            if self.differentiable:
                if self.slang.io_type == IOType.inout:
                    self.access = (AccessType.read, AccessType.readwrite)
                elif self.slang.io_type == IOType.out:
                    self.access = (AccessType.none, AccessType.read)
                else:
                    self.access = (AccessType.read, AccessType.write)
            else:
                if self.slang.io_type == IOType.inout:
                    self.access = (AccessType.read, AccessType.none)
                elif self.slang.io_type == IOType.out:
                    self.access = (AccessType.none, AccessType.none)
                else:
                    self.access = (AccessType.read, AccessType.none)
        else:
            # todo: fwds
            self.access = (AccessType.none, AccessType.none)

    def _get_access(self, prim: PrimType) -> AccessType:
        idx: int = prim.value
        return self.access[idx]

    def gen_call_data_code(self, cg: CodeGen, context: BindContext, depth: int = 0):
        if self.children is not None:
            cgb = cg.call_data_structs

            cgb.begin_struct(f"_t_{self.variable_name}")

            names: list[tuple[Any, ...]] = []
            for field, variable in self.children.items():
                variable_name = variable.gen_call_data_code(cg, context, depth+1)
                if variable_name is not None:
                    names.append(
                        (field, variable_name, variable.slang.primal_type_name, variable.slang.derivative_type_name))

            for name in names:
                cgb.declare(f"_t_{name[1]}", name[1])

            for prim in PrimType:
                if self.access[prim.value] == AccessType.none:
                    continue

                prim_name = prim.name
                prim_type_name = self.vector_type.name
                if prim != PrimType.primal:
                    prim_type_name += ".Differential"

                cgb.empty_line()

                cgb.empty_line()
                cgb.append_line(
                    f"void load_{prim_name}(IContext context, out {prim_type_name} value)")
                cgb.begin_block()
                for name in names:
                    cgb.declare(name[2], name[0])
                    cgb.append_statement(
                        f"this.{name[1]}.load_{prim_name}(ctx(context, _m_{name[1]}),{name[0]})")
                    cgb.assign(f"value.{name[0]}", f"{name[0]}")
                cgb.end_block()

                cgb.empty_line()
                cgb.append_line(
                    f"void store_{prim_name}(IContext context, in {prim_type_name} value)")
                cgb.begin_block()
                for name in names:
                    cgb.append_statement(
                        f"this.{name[1]}.store_{prim_name}(ctx(context, _m_{name[1]}),value.{name[0]})")
                cgb.end_block()

            cgb.end_struct()

            full_map = list(range(context.call_dimensionality))
            if len(full_map) > 0:
                cg.call_data_structs.append_statement(
                    f"static const int[] _m_{self.variable_name} = {{ {','.join([str(x) for x in full_map])} }}")
            else:
                cg.call_data_structs.append_statement(
                    f"static const int _m_{self.variable_name} = 0")

        else:
            # Raise error if attempting to write to non-writable type
            if self.access[0] in [AccessType.write, AccessType.readwrite] and not self.python.writable:
                if depth == 0:
                    raise ValueError(
                        f"Cannot read back value for non-writable type")

            # Generate call data
            self.python.primal.gen_calldata(cg.call_data_structs, context, self)

            if len(self.vector_mapping) > 0:
                cg.call_data_structs.append_statement(
                    f"static const int[] _m_{self.variable_name} = {{ {','.join([str(x) for x in self.vector_mapping.as_tuple()])} }}")
            else:
                cg.call_data_structs.append_statement(
                    f"static const int _m_{self.variable_name} = 0")

        if depth == 0:
            cg.call_data.declare(f"_t_{self.variable_name}", self.variable_name)

        return self.variable_name

    def _gen_trampoline_argument(self):
        arg_def = f"{self.vector_type.name} {self.variable_name}"
        if self.slang.io_type == IOType.inout:
            arg_def = f"inout {arg_def}"
        elif self.slang.io_type == IOType.out:
            arg_def = f"out {arg_def}"
        elif self.slang.io_type == IOType.inn:
            arg_def = f"in {arg_def}"
        if self.slang.no_diff or not self.differentiable:
            arg_def = f"no_diff {arg_def}"
        return arg_def

    def __str__(self) -> str:
        return self._recurse_str(0)

    def _recurse_str(self, depth: int) -> str:
        if self.children is not None:
            child_strs = [
                f"{'  ' * depth}{name}: {child._recurse_str(depth + 1)}" for name, child in self.children.items()]
            return "\n" + "\n".join(child_strs)
        else:
            return f"{self.python.name}"
