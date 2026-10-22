"""前置清洁 IdleTime 的默认值、已有字段兼容及标准接口传递回归。"""

import pytest

from app.backend.execution.plan_builder import _clean_condition, _runtime_clean


@pytest.mark.parametrize("clean_type", ["preclean", "dummy", "dummywac"])
@pytest.mark.parametrize("bounds", [{}, {"lower": 12.5, "upper": 999999}, {"lower": 0, "upper": 0}])
def test_idle_time_bounds_survive_runtime_and_interface(clean_type, bounds):
    """反复展开不能重置已有参数，缺失字段才使用新增默认值。"""
    clean = {"name": "清洁", "cleanType": clean_type, **bounds}
    expected = [bounds.get("lower", 0), bounds.get("upper", 99999)]
    runtime = _runtime_clean(_runtime_clean(clean))
    condition = _clean_condition(runtime)["ExecuteOrder"][0]
    assert condition["StateVariableName"] == "IdleTime"
    assert condition["ThresholdValueList"] == expected
    assert clean == {"name": "清洁", "cleanType": clean_type, **bounds}


@pytest.mark.parametrize("bounds", [
    {"lower": -1}, {"upper": float("inf")}, {"lower": float("nan")},
    {"lower": ""}, {"upper": "oops"}, {"lower": True}, {"lower": 10, "upper": 9},
])
def test_invalid_idle_time_is_rejected(bounds):
    """直接接口调用也必须拒绝非法阈值，不能悄悄回退到默认值。"""
    with pytest.raises(ValueError, match="IdleTime"):
        _clean_condition({"name": "清洁", "cleanType": "preclean", **bounds})
