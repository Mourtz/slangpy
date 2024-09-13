from types import NoneType
from typing import Optional, Union

from sgl import FunctionReflection, ModifierID, VariableReflection

from kernelfunctions.backend import TypeReflection
from kernelfunctions.typeregistry import get_or_create_type
from kernelfunctions.types.basevalueimpl import BaseValueImpl
from kernelfunctions.types.enums import IOType


class SlangFunction:
    def __init__(self, reflection: FunctionReflection) -> NoneType:
        super().__init__()
        self.name = reflection.name
        if reflection.return_type is not None and reflection.return_type.scalar_type != TypeReflection.ScalarType.void:
            self.return_value = SlangValue(reflection)
        else:
            self.return_value = None
        self.parameters = [SlangValue(a) for a in reflection.parameters]
        self.differentiable = reflection.has_modifier(ModifierID.differentiable)


class SlangValue(BaseValueImpl):
    def __init__(self,
                 reflection: Union[FunctionReflection, VariableReflection, TypeReflection.ScalarType],
                 parent: Optional['SlangValue'] = None,
                 name: Optional[str] = None):
        super().__init__()

        if parent is not None:
            # Child value, assume variable or scalar type + inherit modifiers
            assert isinstance(reflection, (VariableReflection, TypeReflection.ScalarType))
            io_type = parent.io_type
            no_diff = parent.no_diff
            if isinstance(reflection, TypeReflection.ScalarType):
                assert name is not None
                self.name = name
                slang_type = reflection
            else:
                assert name is None
                slang_type = reflection.type
                self.name = reflection.name
        if isinstance(reflection, VariableReflection):
            # Function argument - check modifiers
            slang_type = reflection.type
            self.name = reflection.name
            if reflection.has_modifier(ModifierID.inout):
                io_type = IOType.inout
            elif reflection.has_modifier(ModifierID.out):
                io_type = IOType.out
            else:
                io_type = IOType.inn
            no_diff = reflection.has_modifier(ModifierID.nodiff)
        elif isinstance(reflection, FunctionReflection):
            # Just a return value - always out, and only differentiable if function is
            slang_type = reflection.return_type
            self.name = "_result"
            io_type = IOType.out
            no_diff = not reflection.has_modifier(ModifierID.differentiable)

        self.io_type = io_type
        self.no_diff = no_diff
        self.primal = get_or_create_type(slang_type)
        self.derivative = self.primal.differentiate()

        if isinstance(slang_type, TypeReflection):
            if slang_type.kind == TypeReflection.Kind.struct:
                self.fields = {f.name: SlangValue(f, self) for f in slang_type.fields}
            elif slang_type.kind == TypeReflection.Kind.vector:
                self.fields = {f: SlangValue(slang_type.scalar_type, self, f) for f in [
                    "x", "y", "z", "w"][:slang_type.col_count]}
            else:
                self.fields = None
        else:
            self.fields = None

    def gen_trampoline_argument(self, differentiable: bool):
        arg_def = self.argument_declaration
        if self.io_type == IOType.inout:
            arg_def = f"inout {arg_def}"
        elif self.io_type == IOType.out:
            arg_def = f"out {arg_def}"
        elif self.io_type == IOType.inn:
            arg_def = f"in {arg_def}"
        if self.no_diff or not differentiable:
            arg_def = f"no_diff {arg_def}"
        return arg_def
