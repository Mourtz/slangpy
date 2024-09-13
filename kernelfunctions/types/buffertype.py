

from typing import Any, Optional, Sequence
import numpy as np

from sgl import Device
from kernelfunctions.codegen import CodeGenBlock
from kernelfunctions.typeregistry import PYTHON_TYPES, get_or_create_type
from kernelfunctions.types.basetype import BaseType
from kernelfunctions.types.basetypeimpl import BaseTypeImpl
from kernelfunctions.types.basevalue import BaseValue
from kernelfunctions.types.enums import AccessType, PrimType
from kernelfunctions.types.buffer import NDBuffer, NDDifferentiableBuffer

TYPES = r"""
int _idx<let N: int>(int[N] index, int[N] stride) {
    int idx = 0;
    for (int i = 0; i < N; i++) { idx += index[i] * stride[i]; }
    return idx;
}
struct TensorBuffer<T, let N : int> {
    RWStructuredBuffer<T> buffer;
    int[N] strides;
    T get(int[N] index) { return buffer[_idx(index, strides)]; }
    __subscript(int[N] index)->T { get { return get(index); } }
}
struct RWTensorBuffer<T, let N : int> {
    RWStructuredBuffer<T> buffer;
    int[N] strides;
    T get(int[N] index) { return buffer[_idx(index, strides)]; }
    void set(int[N] index, T value) { buffer[_idx(index, strides)] = value; }
    __subscript(int[N] index)->T { get { return get(index); } set { set(index, newValue); } }
}
"""


def _transform_to_subscript(transform: list[Optional[int]]):
    """
    Generates the subscript to be passed into the [] operator when loading or storing
    from the buffer.
    """
    vals = ",".join(
        ("0" if x is None else f"context.call_id[{x}]") for x in transform)
    return f"[{{{vals}}}]"


class NDBufferType(BaseTypeImpl):

    def __init__(self, element_type: BaseType):
        super().__init__()
        self.el_type = element_type

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
        cgb.add_snippet("TensorBuffer", TYPES)  # ensure the types are declared
        tf = _transform_to_subscript(transform)
        cgb.begin_struct(f"_{name}_call_data")
        cgb.type_alias("primal_type", input_value.primal_type_name)
        if access[0] == AccessType.read:
            cgb.declare(f"TensorBuffer<primal_type,{len(transform)}>", "value")
            cgb.append_line(
                f"void load_primal(Context context, out primal_type value) {{ value = this.value{tf}; }}")
        else:
            cgb.declare(f"RWTensorBuffer<primal_type,{len(transform)}>", "value")
            cgb.append_line(
                f"void load_primal(Context context, out primal_type value) {{ value = this.value{tf}; }}")
            cgb.append_line(
                f"void store_primal(Context context, in primal_type value) {{ this.value{tf} = value; }}")
        cgb.end_struct()

    # Call data just returns the primal
    def create_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: NDBuffer) -> Any:
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        return {
            'value': {
                'buffer': data.buffer,
                'strides': list(data.strides)
            }
        }

    # Read back from call data does nothing
    def read_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: NDBuffer, result: Any) -> None:
        pass

    def name(self) -> str:
        return self.el_type.name()

    def element_type(self, value: Optional[NDBuffer] = None):
        return self.el_type

    def container_shape(self, value: Optional[NDBuffer] = None):
        if value is not None:
            return value.shape
        else:
            return None

    def shape(self, value: Any = None):
        if value is not None:
            return super().shape(value)
        else:
            return None

    def differentiable(self, value: Optional[NDBuffer] = None):
        return self.el_type.differentiable()

    def differentiate(self, value: Optional[NDBuffer] = None):
        return self.el_type.differentiate()

    def create_output(self, device: Device, call_shape: Sequence[int]) -> Any:
        return None

    def read_output(self, device: Device, data: NDBuffer) -> Any:
        return data


def create_vr_type_for_value(value: Any):
    assert isinstance(value, NDBuffer)
    return NDBufferType(get_or_create_type(value.element_type))


PYTHON_TYPES[NDBuffer] = create_vr_type_for_value


class NDDifferentiableBufferType(BaseTypeImpl):

    def __init__(self, element_type: BaseType):
        super().__init__()
        self.el_type = element_type

    # Values don't store a derivative - they're just a value
    def has_derivative(self, value: Any = None) -> bool:
        return True

    # Refs can be written to!
    def is_writable(self, value: Any = None) -> bool:
        return True

    # Call data can only be read access to primal, and simply declares it as a variable
    def gen_calldata(self, cgb: CodeGenBlock, input_value: 'BaseValue', name: str, transform: list[Optional[int]], access: tuple[AccessType, AccessType]):
        cgb.add_snippet("TensorBuffer", TYPES)  # ensure the types are declared
        cgb.begin_struct(f"_{name}_call_data")
        cgb.type_alias(f"primal_type", input_value.primal_type_name)
        cgb.type_alias(f"derivative_type", input_value.derivative_type_name)
        tf = _transform_to_subscript(transform)
        for prim in PrimType:
            prim_name = prim.name
            prim_access = access[prim.value]
            if prim_access == AccessType.none:
                continue
            if prim_access == AccessType.read:
                cgb.declare(f"TensorBuffer<{prim_name}_type,{len(transform)}>", prim_name)
                cgb.append_line(
                    f"void load_{prim_name}(Context context, out {prim_name}_type value) {{ value = this.{prim_name}{tf}; }}")
            else:
                cgb.declare(
                    f"RWTensorBuffer<{prim_name}_type,{len(transform)}>", prim_name)
                cgb.append_line(
                    f"void load_{prim_name}(Context context, out {prim_name}_type value) {{ value = this.{prim_name}{tf}; }}")
                cgb.append_line(
                    f"void store_{prim_name}(Context context, in {prim_name}_type value) {{ this.{prim_name}{tf} = value; }}")
        cgb.end_struct()

    # Call data just returns the primal

    def create_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: NDDifferentiableBuffer) -> Any:
        assert access[0] != AccessType.none
        assert access[1] == AccessType.none
        return {
            'value': {
                'buffer': data.buffer,
                'strides': list(data.strides)
            }
        }

    # Read back from call data does nothing
    def read_calldata(self, device: Device, input_value: 'BaseValue', access: tuple[AccessType, AccessType], data: NDDifferentiableBuffer, result: Any) -> None:
        pass

    def name(self) -> str:
        return self.el_type.name()

    def element_type(self, value: Optional[NDDifferentiableBuffer] = None):
        return self.el_type

    def container_shape(self, value: Optional[NDDifferentiableBuffer] = None):
        if value is not None:
            return value.shape
        else:
            return None

    def shape(self, value: Any = None):
        if value is not None:
            return super().shape(value)
        else:
            return None

    def differentiable(self, value: Optional[NDDifferentiableBuffer] = None):
        return self.el_type.differentiable()

    def differentiate(self, value: Optional[NDDifferentiableBuffer] = None):
        return self.el_type.differentiate()

    def create_output(self, device: Device, call_shape: Sequence[int]) -> Any:
        return None

    def read_output(self, device: Device, data: NDDifferentiableBuffer) -> Any:
        return data


def create_gradvr_type_for_value(value: Any):
    assert isinstance(value, NDDifferentiableBuffer)
    return NDDifferentiableBufferType(get_or_create_type(value.element_type))


PYTHON_TYPES[NDDifferentiableBuffer] = create_gradvr_type_for_value
