from typing import Any, Callable, Optional, Protocol, TYPE_CHECKING

from kernelfunctions.core import SlangFunction

from kernelfunctions.backend import SlangModule, DeclReflection, TypeReflection, FunctionReflection, slangpynative
from kernelfunctions.shapes import TConcreteShape
from kernelfunctions.typeregistry import PYTHON_SIGNATURES

if TYPE_CHECKING:
    from kernelfunctions.calldata import CallData
    from kernelfunctions.struct import Struct

ENABLE_CALLDATA_CACHE = True
CALL_DATA_CACHE: dict[str, 'CallData'] = {}


def _cache_value_to_id(val: Any) -> str:
    cb = PYTHON_SIGNATURES[type(val)]
    if cb is None:
        return ""
    else:
        return cb(val)


class IThis(Protocol):
    def get_this(self) -> Any:
        ...

    def update_this(self, value: Any) -> None:
        ...


class FunctionChainBase:
    def __init__(self, parent: Optional["FunctionChainBase"]) -> None:
        super().__init__()
        self.parent = parent
        self.slangpy_signature = f"{parent.slangpy_signature}." if parent is not None else ""

    def call(self, *args: Any, **kwargs: Any) -> Any:
        calldata = self._build_call_data(*args, **kwargs)
        return calldata.call(*args, **kwargs)

    @property
    def bwds_diff(self) -> Any:
        return FunctionChainBwdsDiff(self)

    def set(self, *args: Any, **kwargs: Any):
        return FunctionChainSet(self, *args, **kwargs)

    def transform_input(self, transforms: dict[str, TConcreteShape]):
        return FunctionChainInputTransform(self, transforms)

    def transform_output(self, transforms: dict[str, TConcreteShape]):
        return FunctionChainOutputTransform(self, transforms)

    def instance(self, this: IThis):
        return FunctionChainThis(self, this)

    def debug_build_call_data(self, *args: Any, **kwargs: Any):
        return self._build_call_data(*args, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any):
        return self.call(*args, **kwargs)

    def _build_call_data(self, *args: Any, **kwargs: Any):
        this = None
        current = self
        while current is not None:
            if isinstance(current, FunctionChainThis):
                this = current.this
                break
            current = current.parent

        sig = slangpynative.hash_signature(
            _cache_value_to_id, self, this, *args, **kwargs)
        # print(sig)
        if ENABLE_CALLDATA_CACHE and sig in CALL_DATA_CACHE:
            return CALL_DATA_CACHE[sig]

        chain = []
        current = self
        while current is not None:
            chain.append(current)
            current = current.parent
        chain.reverse()

        from .calldata import CallData
        res = CallData(chain, *args, **kwargs)
        if ENABLE_CALLDATA_CACHE:
            CALL_DATA_CACHE[sig] = res
        return res

    def as_func(self) -> 'FunctionChainBase':
        return self

    def as_struct(self) -> 'Struct':
        raise ValueError("Cannot convert a function to a struct")


class FunctionChainSet(FunctionChainBase):
    def __init__(self, parent: FunctionChainBase, *args: Any, **kwargs: Any) -> None:
        super().__init__(parent)
        self.props: Optional[dict[str, Any]] = None
        self.callback: Optional[Callable] = None  # type: ignore

        if len(args) > 0 and len(kwargs) > 0:
            raise ValueError(
                "Set accepts either positional or keyword arguments, not both"
            )
        if len(args) > 1:
            raise ValueError(
                "Set accepts only one positional argument (a dictionary or callback)"
            )

        if len(kwargs) > 0:
            self.props = kwargs
        elif len(args) > 0:
            if callable(args[0]):
                self.callback = args[0]
            elif isinstance(args[0], dict):
                self.props = args[0]
            else:
                raise ValueError(
                    "Set requires a dictionary or callback as a single positional argument"
                )
        else:
            raise ValueError("Set requires at least one argument")


class FunctionChainInputTransform(FunctionChainBase):
    def __init__(
        self, parent: FunctionChainBase, transforms: dict[str, TConcreteShape]
    ) -> None:
        super().__init__(parent)
        self.transforms = transforms


class FunctionChainOutputTransform(FunctionChainBase):
    def __init__(
        self, parent: FunctionChainBase, transforms: dict[str, TConcreteShape]
    ) -> None:
        super().__init__(parent)
        self.transforms = transforms


class FunctionChainThis(FunctionChainBase):
    def __init__(self, parent: FunctionChainBase, this: IThis) -> None:
        super().__init__(parent)
        self.this = this


class FunctionChainBwdsDiff(FunctionChainBase):
    def __init__(self, parent: FunctionChainBase) -> None:
        super().__init__(parent)

# A callable kernel function. This assumes the function is in the root
# of the module, however a parent in the abstract syntax tree can be provided
# to search for the function in a specific scope.


class Function(FunctionChainBase):
    def __init__(
        self,
        module: SlangModule,
        name: str,
        type_parent: Optional[str] = None,
        type_reflection: Optional[TypeReflection] = None,
        func_reflections: Optional[list[FunctionReflection]] = None,
    ) -> None:
        super().__init__(None)
        self.module = module
        self.name = name

        # If type parent supplied by name, look it up
        if type_parent is not None:
            type_reflection = module.layout.find_type_by_name(type_parent)
            if type_reflection is None:
                raise ValueError(
                    f"Type '{type_parent}' not found in module {module.name}")

        # If function reflections not supplied, look up either from type or module
        if func_reflections is None:
            if type_reflection is None:
                # With no type parent, use the module's ast to find functions
                ast_functions = module.module_decl.find_children_of_kind(
                    DeclReflection.Kind.func, name
                )
                if len(ast_functions) == 0:
                    raise ValueError(
                        f"Function '{name}' not found in module {module.name}")
                func_reflections = [x.as_function() for x in ast_functions]
            else:
                # With a type parent, look up the function in the type
                func_reflection = module.layout.find_function_by_name_in_type(
                    type_reflection, name
                )
                if func_reflection is None:
                    raise ValueError(
                        f"Function '{name}' not found in type '{type_parent}' in module {module.name}"
                    )
                func_reflections = [func_reflection]

        # Store type parent name if found
        self.type_parent = type_reflection.full_name if type_reflection is not None else None

        # Build and store overloads
        self.overloads = [SlangFunction(x, type_reflection) for x in func_reflections]

        self.slangpy_signature = f"[{self.type_parent or ''}::{self.name}]"
