

from typing import Any, Optional
import numpy as np

from kernelfunctions.core import CodeGenBlock, BaseType, BaseTypeImpl, BoundVariable, AccessType, BoundVariableRuntime, CallContext

from kernelfunctions.shapes import TLooseShape
from kernelfunctions.types import ValueRef

from kernelfunctions.backend import Buffer, ResourceUsage
from kernelfunctions.typeregistry import PYTHON_TYPES, get_or_create_type


class ValueRefType(BaseTypeImpl):

    def __init__(self, value_type: BaseType):
        super().__init__()
        self.value_type = value_type

    # Values don't store a derivative - they're just a value
    @property
    def has_derivative(self) -> bool:
        return False

    # Refs can be written to!
    @property
    def is_writable(self) -> bool:
        return True

    # Call data can only be read access to primal, and simply declares it as a variable
    def gen_calldata(self, cgb: CodeGenBlock, binding: 'BoundVariable'):
        access = binding.access
        name = binding.variable_name
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        if access[0] == AccessType.read:
            cgb.type_alias(f"_{name}", f"ValueRef<{self.value_type.name}>")
        else:
            cgb.type_alias(
                f"_{name}", f"RWValueRef<{self.value_type.name}>")

    # Call data just returns the primal

    def create_calldata(self, context: CallContext, binding: 'BoundVariableRuntime', data: ValueRef) -> Any:
        access = binding.access
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        if access[0] == AccessType.read:
            return {'value': data.value}
        else:
            npdata = self.value_type.to_numpy(data.value).view(dtype=np.uint8)
            return {
                'value': context.device.create_buffer(element_count=1, struct_size=npdata.size, data=npdata, usage=ResourceUsage.shader_resource | ResourceUsage.unordered_access)
            }

    # Read back from call data does nothing
    def read_calldata(self, context: CallContext, binding: 'BoundVariableRuntime', data: ValueRef, result: Any) -> None:
        access = binding.access
        if access[0] in [AccessType.write, AccessType.readwrite]:
            assert isinstance(result['value'], Buffer)
            npdata = result['value'].to_numpy()
            data.value = self.value_type.from_numpy(npdata)

    @property
    def name(self) -> str:
        return self.value_type.name

    @property
    def element_type(self):
        return self.value_type.element_type

    def get_shape(self, value: Optional[ValueRef] = None) -> TLooseShape:
        return self.value_type.get_shape()

    @property
    def differentiable(self):
        return self.value_type.differentiable

    @property
    def derivative(self):
        return self.value_type.derivative

    def create_output(self, context: CallContext) -> Any:
        pt = self.value_type.python_return_value_type
        if pt is not None:
            return ValueRef(pt())
        else:
            return ValueRef(None)

    def read_output(self, context: CallContext, data: ValueRef) -> Any:
        return data.value


def create_vr_type_for_value(value: Any):
    assert isinstance(value, ValueRef)
    return ValueRefType(get_or_create_type(type(value.value)))


PYTHON_TYPES[ValueRef] = create_vr_type_for_value
