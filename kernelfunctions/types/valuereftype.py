

from typing import Any, Optional, Sequence
import numpy as np

from sgl import Buffer, Device, ResourceUsage
from kernelfunctions.codegen import CodeGenBlock
from kernelfunctions.typeregistry import PYTHON_TYPES, get_or_create_type
from kernelfunctions.types.basetype import BaseType
from kernelfunctions.types.basetypeimpl import BaseTypeImpl
from kernelfunctions.types.basevalue import BaseValue
from kernelfunctions.types.enums import AccessType
from kernelfunctions.types.valueref import ValueRef


class ValueRefType(BaseTypeImpl):

    def __init__(self, value_type: BaseType):
        super().__init__()
        self.value_type = value_type

    # Values don't store a derivative - they're just a value
    def has_derivative(self, value: Any = None) -> bool:
        return False

    # Refs can be written to!
    def is_writable(self, value: Any = None) -> bool:
        return True

    # Call data can only be read access to primal, and simply declares it as a variable
    def gen_calldata(self, cgb: CodeGenBlock, input_value: 'BaseValue', name: str, transform: list[Optional[int]], access: tuple[AccessType, AccessType]):
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        cgb.begin_struct(f"_{name}_call_data")
        cgb.type_alias("primal_type", input_value.primal_type_name)
        if access[0] == AccessType.read:
            cgb.declare("primal_type", "value")
            cgb.append_line(
                "void load_primal(Context context, out primal_type value) { value = this.value; }")
        else:
            cgb.declare(f"RWStructuredBuffer<primal_type>", "value")
            cgb.append_line(
                "void load_primal(Context context, out primal_type value) { value = this.value[0]; }")
            cgb.append_line(
                "void store_primal(Context context, in primal_type value) { this.value[0] = value; }")
        cgb.end_struct()

    # Load should only ever be reading the primal directly from the call data
    def gen_load_store(self, cgb: CodeGenBlock, input_value: 'BaseValue', name: str, transform: list[Optional[int]],  access: tuple[AccessType, AccessType]):
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none

        cgb.begin_struct(f"_{name}")
        cgb.type_alias("primal_type", input_value.primal_type_name)
        if access[0] in [AccessType.read, AccessType.readwrite]:
            cgb.append_line(
                f"static void load_primal(Context context, out primal_type value) {{ call_data.{name}.load_primal(context,value); }}")
        if access[0] in [AccessType.write, AccessType.readwrite]:
            cgb.append_line(
                f"static void store_primal(Context context, in primal_type value) {{ call_data.{name}.store_primal(context,value); }}")
        cgb.end_struct()

    # Call data just returns the primal

    def create_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: ValueRef) -> Any:
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        if access[0] == AccessType.read:
            return {'value': data.value}
        else:
            npdata = self.value_type.to_numpy(data.value).view(dtype=np.uint8)
            return {
                'value': device.create_buffer(element_count=1, struct_size=npdata.size, data=npdata, usage=ResourceUsage.shader_resource | ResourceUsage.unordered_access)
            }

    # Read back from call data does nothing
    def read_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: ValueRef, result: Any) -> None:
        if access[0] in [AccessType.write, AccessType.readwrite]:
            assert isinstance(result['value'], Buffer)
            npdata = result['value'].to_numpy()
            data.value = self.value_type.from_numpy(npdata)

    def name(self) -> str:
        return self.value_type.name()

    def element_type(self, value: Optional[ValueRef] = None):
        return self.value_type.element_type()

    def shape(self, value: Optional[ValueRef] = None):
        return self.value_type.shape()

    def differentiable(self, value: Optional[ValueRef] = None):
        return self.value_type.differentiable()

    def differentiate(self, value: Optional[ValueRef] = None):
        return self.value_type.differentiate()

    def create_output(self, device: Device, call_shape: Sequence[int]) -> Any:
        return ValueRef(None)

    def read_output(self, device: Device, data: ValueRef) -> Any:
        return data.value


def create_vr_type_for_value(value: Any):
    assert isinstance(value, ValueRef)
    return ValueRefType(get_or_create_type(type(value.value)))


PYTHON_TYPES[ValueRef] = create_vr_type_for_value
