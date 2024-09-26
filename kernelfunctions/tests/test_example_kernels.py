from pathlib import Path
from time import time
import numpy as np
import pytest
from sgl import ShaderCursor
import kernelfunctions as kf
from kernelfunctions.types import NDDifferentiableBuffer
from kernelfunctions.tests import helpers
from helpers import test_id  # type: ignore (pytest fixture)
from kernelfunctions.tests.test_differential_function_call import python_eval_polynomial, python_eval_polynomial_a_deriv, python_eval_polynomial_b_deriv
from kernelfunctions.backend import DeviceType, float3, float1, uint3

# pyright: reportOptionalMemberAccess=false, reportArgumentType=false


def rand_array_of_floats(size: int):
    return np.random.rand(size).astype(np.float32)


@pytest.mark.skip(reason="Test for slang issue")
@pytest.mark.parametrize("device_type", helpers.DEFAULT_DEVICE_TYPES)
def test_read_slice_error(test_id: str, device_type: DeviceType):

    device = helpers.get_device(device_type)

    prim_program = device.load_program(
        str(Path(__file__).parent / "generated_tests/read_slice_generic_error.slang"), ["main"])

    assert prim_program is not None

# Verify a 'hard coded' example of a generated kernel compiles and runs
# correctly.


@pytest.mark.parametrize("device_type", helpers.DEFAULT_DEVICE_TYPES)
def test_buffered_scalar_function(test_id: str, device_type: DeviceType):

    device = helpers.get_device(device_type)

    user_func_module = device.load_module_from_source(
        f"user_module_{test_id}",
        r"""
[Differentiable]
void user_func(float a, float b, out float c) {
    c = a*a + b + 1;
}
""",
    )

    # Load the example shader, with the custom user function at the top.
    generated_module = device.load_module_from_source(
        f"generated_module_{test_id}",
        f'import "user_module_{test_id}";\n'
        + open(Path(__file__).parent / "test_example_kernel_scalar.slang").read(),
    )

    # Create the forward and backward kernels.
    ep = generated_module.entry_point("main")
    program = device.link_program([generated_module, user_func_module], [ep])
    kernel = device.create_compute_kernel(program)
    backwards_ep = generated_module.entry_point("main_backwards")
    backwards_program = device.link_program(
        [generated_module, user_func_module], [backwards_ep]
    )
    backwards_kernel = device.create_compute_kernel(backwards_program)

    # Create input buffer 0 with random numbers and an empty gradient buffer (ignored).
    in_buffer_0 = kf.NDDifferentiableBuffer(
        element_count=64, device=device, element_type=float, requires_grad=True
    )
    in_buffer_0.buffer.from_numpy(rand_array_of_floats(in_buffer_0.element_count))
    in_buffer_0.grad.buffer.from_numpy(
        np.zeros(in_buffer_0.element_count, dtype=np.float32))  # type: ignore

    # Same with input buffer 1.
    in_buffer_1 = kf.NDDifferentiableBuffer(
        element_count=64, device=device, element_type=float, requires_grad=True
    )
    in_buffer_1.buffer.from_numpy(rand_array_of_floats(in_buffer_1.element_count))
    in_buffer_1.grad.buffer.from_numpy(
        np.zeros(in_buffer_1.element_count, dtype=np.float32))  # type: ignore

    # Create empty output buffer with gradients initialized to 1 (as there is 1-1 correspondence between
    # output of user function and output of kernel)
    out_buffer = kf.NDDifferentiableBuffer(
        element_count=64, device=device, element_type=float, requires_grad=True
    )
    out_buffer.buffer.from_numpy(np.zeros(out_buffer.element_count, dtype=np.float32))
    out_buffer.grad.buffer.from_numpy(
        np.ones(out_buffer.element_count, dtype=np.float32))  # type: ignore

    # Dispatch the forward kernel.
    kernel.dispatch(
        uint3(64, 1, 1),
        {
            "call_data": {
                "a": in_buffer_0.buffer,
                "b": in_buffer_1.buffer,
                "c": out_buffer.buffer,
            }
        },
    )

    # Read and validate forward kernel results (expecting c = a*a + b + 1)
    in_data_0 = in_buffer_0.buffer.to_numpy().view(np.float32)
    in_data_1 = in_buffer_1.buffer.to_numpy().view(np.float32)
    out_data = out_buffer.buffer.to_numpy().view(np.float32)
    eval_data = in_data_0 * in_data_0 + in_data_1 + 1
    assert np.allclose(out_data, eval_data)

    # Dispatch the backward kernel.
    backwards_kernel.dispatch(
        uint3(64, 1, 1),
        {
            "call_data": {
                "a": in_buffer_0.buffer,
                "a_grad": in_buffer_0.grad.buffer,
                "b": in_buffer_1.buffer,
                "b_grad": in_buffer_1.grad.buffer,
                "c": out_buffer.buffer,
                "c_grad": out_buffer.grad.buffer,
            }
        },
    )

    # Read and validate backward kernel results (expecting a_grad = 2*a, b_grad = 1)
    in_grad_0 = in_buffer_0.grad.buffer.to_numpy().view(np.float32)  # type: ignore
    in_grad_1 = in_buffer_1.grad.buffer.to_numpy().view(np.float32)  # type: ignore
    eval_grad_0 = 2 * in_data_0
    eval_grad_1 = np.ones(in_data_1.shape)
    assert np.allclose(in_grad_0, eval_grad_0)
    assert np.allclose(in_grad_1, eval_grad_1)


@pytest.mark.parametrize("device_type", helpers.DEFAULT_DEVICE_TYPES)
def test_vec3_call_with_buffers_soa(device_type: DeviceType):

    device = helpers.get_device(device_type)

    prim_program = device.load_program(
        str(Path(__file__).parent / "generated_tests/polynomial_soa.slang"), ["main"])
    kernel_eval_polynomial = device.create_compute_kernel(prim_program)

    bwds_program = device.load_program(
        str(Path(__file__).parent / "generated_tests/polynomial_soa_backwards.slang"), ["main"])
    kernel_eval_polynomial_backwards = device.create_compute_kernel(bwds_program)

    a_x = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_x.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_y = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_y.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_z = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_z.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    b = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )
    b.buffer.from_numpy(np.random.rand(32*3).astype(np.float32))

    res = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )

    total_threads = 32

    call_data = {
        'a__x_primal': {'buffer': a_x.buffer, 'strides': list(a_x.strides)},
        'a__y_primal': {'buffer': a_y.buffer, 'strides': list(a_y.strides)},
        'a__z_primal': {'buffer': a_z.buffer, 'strides': list(a_z.strides)},
        'b_primal': {'buffer': b.buffer, 'strides': list(b.strides)},
        '_result_primal': {'buffer': res.buffer, 'strides': list(res.strides)},
        '_call_stride': [1],
        '_call_dim': [32],
        '_thread_count': uint3(total_threads, 1, 1)
    }

    # Dispatch the kernel.
    kernel_eval_polynomial.dispatch(uint3(total_threads, 1, 1), {"call_data": call_data})

    a_x_data = a_x.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_y_data = a_y.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_z_data = a_z.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_data = np.column_stack((a_x_data, a_y_data, a_z_data))
    b_data = b.buffer.to_numpy().view(np.float32).reshape(-1, 3)
    expected = python_eval_polynomial(a_data, b_data)
    res_data = res.buffer.to_numpy().view(np.float32).reshape(-1, 3)

    assert np.allclose(res_data, expected)

    res.grad.buffer.from_numpy(np.ones(32*3, dtype=np.float32))

    call_data = {
        'a__x_primal': {'buffer': a_x.buffer, 'strides': list(a_x.strides)},
        'a__x_derivative': {'buffer': a_x.grad.buffer, 'strides': list(a_x.strides)},
        'a__y_primal': {'buffer': a_y.buffer, 'strides': list(a_y.strides)},
        'a__y_derivative': {'buffer': a_y.grad.buffer, 'strides': list(a_y.strides)},
        'a__z_primal': {'buffer': a_z.buffer, 'strides': list(a_z.strides)},
        'a__z_derivative': {'buffer': a_z.grad.buffer, 'strides': list(a_z.strides)},
        'b_primal': {'buffer': b.buffer, 'strides': list(b.strides)},
        'b_derivative': {'buffer': b.grad.buffer, 'strides': list(b.strides)},
        '_result_derivative': {'buffer': res.grad.buffer, 'strides': list(res.strides)},
        '_call_stride': [1],
        '_call_dim': [32],
        '_thread_count': uint3(total_threads, 1, 1)
    }

    kernel_eval_polynomial_backwards.dispatch(
        uint3(total_threads, 1, 1), {"call_data": call_data})

    a_x_grad_data = a_x.grad.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_y_grad_data = a_y.grad.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_z_grad_data = a_z.grad.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_grad_data = np.column_stack((a_x_grad_data, a_y_grad_data, a_z_grad_data))
    b_grad_data = b.grad.buffer.to_numpy().view(np.float32).reshape(-1, 3)

    exprected_grad = python_eval_polynomial_a_deriv(a_data, b_data)
    assert np.allclose(a_grad_data, exprected_grad)

    exprected_grad = python_eval_polynomial_b_deriv(a_data, b_data)
    assert np.allclose(b_grad_data, exprected_grad)


@pytest.mark.parametrize("device_type", helpers.DEFAULT_DEVICE_TYPES)
def test_vec3_nested_calldata_soa(device_type: DeviceType):

    device = helpers.get_device(device_type)

    prim_program = device.load_program(
        str(Path(__file__).parent / "nested_types.slang"), ["main"])
    kernel_eval_polynomial = device.create_compute_kernel(prim_program)

    a_x = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_x.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_y = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_y.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_z = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_z.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    b = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )
    b.buffer.from_numpy(np.random.rand(32*3).astype(np.float32))

    res = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )

    total_threads = 32

    call_data = {
        'a': {
            'x': {'primal': {'buffer': a_x.buffer, 'strides': list(a_x.strides)}},
            'y': {'primal': {'buffer': a_y.buffer, 'strides': list(a_y.strides)}},
            'z': {'primal': {'buffer': a_z.buffer, 'strides': list(a_z.strides)}}
        },
        'b': {'primal': {'buffer': b.buffer, 'strides': list(b.strides)}},
        '_result': {'primal': {'buffer': res.buffer, 'strides': list(res.strides)}},
        '_call_stride': [1],
        '_call_dim': [32],
        '_thread_count': uint3(total_threads, 1, 1)
    }

    # Dispatch the kernel.
    kernel_eval_polynomial.dispatch(uint3(total_threads, 1, 1), {"call_data": call_data})

    a_x_data = a_x.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_y_data = a_y.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_z_data = a_z.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_data = np.column_stack((a_x_data, a_y_data, a_z_data))
    b_data = b.buffer.to_numpy().view(np.float32).reshape(-1, 3)
    expected = python_eval_polynomial(a_data, b_data)
    res_data = res.buffer.to_numpy().view(np.float32).reshape(-1, 3)

    assert np.allclose(res_data, expected)


@pytest.mark.parametrize("device_type", helpers.DEFAULT_DEVICE_TYPES)
def test_vec3_nested_calldata_soa_generics(device_type: DeviceType):

    device = helpers.get_device(device_type)

    prim_program = device.load_program(
        str(Path(__file__).parent / "nested_types_generics.slang"), ["main"])
    kernel_eval_polynomial = device.create_compute_kernel(prim_program)

    a_x = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_x.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_y = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_y.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    a_z = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float1,
        requires_grad=True,
    )
    a_z.buffer.from_numpy(np.random.rand(32).astype(np.float32))

    b = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )
    b.buffer.from_numpy(np.random.rand(32*3).astype(np.float32))

    res = NDDifferentiableBuffer(
        element_count=32,
        device=device,
        element_type=float3,
        requires_grad=True,
    )

    pipeline = device.create_compute_pipeline(prim_program)

    command_buffer = device.create_command_buffer()
    with command_buffer.encode_compute_commands() as encoder:
        start = time()
        count = 10000
        for i in range(0, count):
            shader_object = encoder.bind_pipeline(pipeline)
            processor = ShaderCursor(shader_object)['call_data']
            processor['_thread_count'] = uint3(32, 1, 1)
            encoder.dispatch([32, 1, 1])
        end = time()
        print(f"Time taken per add: {1000.0*(end-start)/count}ms")

    total_threads = 32

    call_data = {
        'a': {
            'x': {'buffer': a_x.buffer, 'strides': list(a_x.strides)},
            'y': {'buffer': a_y.buffer, 'strides': list(a_y.strides)},
            'z': {'buffer': a_z.buffer, 'strides': list(a_z.strides)}
        },
        'b': {'buffer': b.buffer, 'strides': list(b.strides)},
        '_result': {'buffer': res.buffer, 'strides': list(res.strides)},
        '_call_stride': [1],
        '_call_dim': [32],
        '_thread_count': uint3(total_threads, 1, 1)
    }

    # Dispatch the kernel.
    kernel_eval_polynomial.dispatch(uint3(total_threads, 1, 1), {"call_data": call_data})

    a_x_data = a_x.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_y_data = a_y.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_z_data = a_z.buffer.to_numpy().view(np.float32).reshape(-1, 1)
    a_data = np.column_stack((a_x_data, a_y_data, a_z_data))
    b_data = b.buffer.to_numpy().view(np.float32).reshape(-1, 3)
    expected = python_eval_polynomial(a_data, b_data)
    res_data = res.buffer.to_numpy().view(np.float32).reshape(-1, 3)

    assert np.allclose(res_data, expected)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
