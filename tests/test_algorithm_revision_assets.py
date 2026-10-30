"""算法版本指纹覆盖实际推理模型及 ONNX 外部权重，不依赖模型加载环境。"""

import pytest

from app.backend.algorithms.interface import algorithm_revision


@pytest.mark.parametrize("name", ["single.onnx", "single.onnx_data", "single.onnx.data"])
def test_model_content_change_invalidates_package_revision(tmp_path, name):
    """仅替换推理资产也必须使缓存失效，不能沿用旧模型的会话。"""
    model = tmp_path / "models" / name
    model.parent.mkdir()
    model.write_bytes(b"old-model")
    first = algorithm_revision(tmp_path)
    model.write_bytes(b"new-model")
    assert algorithm_revision(tmp_path) != first


def test_runtime_model_diagnostics_do_not_invalidate_package_revision(tmp_path):
    """日志目录即使写入同后缀诊断文件，也不能造成运行中热重载。"""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "single.onnx").write_bytes(b"model")
    first = algorithm_revision(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "dump.onnx").write_bytes(b"diagnostic")
    assert algorithm_revision(tmp_path) == first
