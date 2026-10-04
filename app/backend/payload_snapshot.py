"""标准接口 payload 的隔离复制。

JSON 字典、数组和标量走专用复制路径，避免逐个标量进入通用 deepcopy 分派。
非 JSON 扩展对象仍使用 deepcopy；共享引用、循环引用与字典键的类型均保留。
本模块不缓存业务数据，也不改变接口序列化或持久化格式。
"""

from copy import deepcopy
from typing import Any, TypeVar


Payload = TypeVar("Payload")
_SCALAR_TYPES = frozenset({str, int, float, bool, type(None)})


def copy_payload(value: Payload) -> Payload:
    """返回与输入完全隔离的副本，保留扩展类型及输入内部的引用关系。

    参数 value 可以是标准 JSON 数据或包含非 JSON 扩展的接口对象；返回值保持
    原类型。复制只使用本次调用的 memo，不跨请求共享任何可变对象。
    """
    return _copy_value(value, {})


def _copy_value(value: Any, memo: dict[int, Any]) -> Any:
    """复制可变容器，直接复用不可变 JSON 标量。"""
    value_type = type(value)
    if value_type in _SCALAR_TYPES:
        return value
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if value_type is dict:
        result: dict = {}
        memo[identity] = result
        for key, item in value.items():
            copied_key = key if type(key) in _SCALAR_TYPES else deepcopy(key, memo)
            result[copied_key] = item if type(item) in _SCALAR_TYPES else _copy_value(item, memo)
        return result
    if value_type is list:
        result_list: list = []
        memo[identity] = result_list
        result_list.extend([item if type(item) in _SCALAR_TYPES else _copy_value(item, memo) for item in value])
        return result_list
    return deepcopy(value, memo)
