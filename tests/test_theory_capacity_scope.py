"""独立理论产能的范围契约：暂停清洁、有限批次与排程周期请求，不静默降级。"""
from http import HTTPStatus
from unittest.mock import Mock

import pytest

from app.backend.api.http import ConfigEditorHandler
from app.backend.theory_capacity_presets import read_theory_capacity_preset


@pytest.mark.parametrize('clean', ['preclean', 'postclean', 'wacclean', 'dummy', 'dummywac'])
@pytest.mark.parametrize('mode', ['steady', 'periodic', 'batch'])
def test_theory_capacity_rejects_cleaning_without_reading_device(clean, mode):
    """所有原清洁类型均明确拒绝，包括旧客户端的三种口径；不能返回无清洁结果冒充。"""
    with pytest.raises(ValueError, match='清洁及有限批次计算暂不支持'):
        read_theory_capacity_preset('unused-device', cleaning_values={'clean': clean, 'mode': mode})


@pytest.mark.parametrize('mode', ['periodic', 'batch', 'invalid'])
def test_theory_capacity_rejects_nonsteady_mode(mode):
    """没有指定清洁时也不接受已暂停的批次/排程入口。"""
    with pytest.raises(ValueError, match='当前仅支持无清洁稳态计算'):
        read_theory_capacity_preset('unused-device', cleaning_values={'mode': mode})


def test_theory_capacity_http_returns_clear_unsupported_response(monkeypatch):
    """旧清洁请求通过正式 HTTP 边界返回 400 与说明，不触发 worker 或调度器。"""
    monkeypatch.setattr("app.backend.api.http.BUILTIN_ALGORITHM_AVAILABLE", False)
    handler = ConfigEditorHandler.__new__(ConfigEditorHandler)
    handler._send_json = Mock()
    handler.path = '/api/theory-capacity/preset?device=unused-device&clean=preclean&mode=periodic'
    handler.do_GET()
    payload, status = handler._send_json.call_args.args
    assert status == HTTPStatus.BAD_REQUEST
    assert payload['ok'] is False
    assert '清洁及有限批次计算暂不支持' in payload['error']


@pytest.mark.parametrize('endpoint,reader,query', [
    ('preset', 'read_theory_capacity_preset', 'device=test'),
    ('route-index', 'read_theory_capacity_route_index', 'device=test'),
    ('scan', 'read_theory_capacity_scan', 'device=test'),
    ('derivation', 'read_theory_capacity_derivation', 'device=test&route=route'),
])
def test_theory_capacity_http_dispatches_without_algorithm(monkeypatch, endpoint, reader, query):
    """四个正式 HTTP 入口在算法不可用时仍进入平台服务，防止遗留可用性门禁。"""
    monkeypatch.setattr('app.backend.api.http.BUILTIN_ALGORITHM_AVAILABLE', False)
    calculate = Mock(return_value={'ok': True, 'independent': True})
    monkeypatch.setattr('app.backend.api.http.' + reader, calculate)
    handler = ConfigEditorHandler.__new__(ConfigEditorHandler)
    handler._send_json = Mock()
    handler.path = '/api/theory-capacity/' + endpoint + '?' + query
    handler.do_GET()
    calculate.assert_called_once()
    handler._send_json.assert_called_once_with({'ok': True, 'independent': True})
