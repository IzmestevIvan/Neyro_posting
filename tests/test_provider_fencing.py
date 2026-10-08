import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest

spec = importlib.util.spec_from_file_location('provider_fencing', Path(__file__).resolve().parents[1] / 'scripts/ha/provider_fencing.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
INVENTORY = {'old': {'server_id': 'srv-1', 'panel_id': 'vm-1'}, 'new': {'server_id': 'srv-2', 'panel_id': 'vm-2'}}
INCIDENT = 'incident-test-123456'

def status(power='active', **changes):
    return dict({'ok': True, 'id': 'srv-1', 'panel_server_id': 'vm-1', 'state': power, 'status': power, 'live_data_available': True}, **changes)

@pytest.mark.parametrize('changes', [dict(ok=False), dict(id='srv-2'), dict(panel_server_id='vm-2'), dict(live_data_available=False), dict(state='deleted'), dict(status='active')])
def test_uncertain_status_never_authorizes_promotion(changes):
    provider = Mock()
    provider.request.return_value = status('stopped', **changes)
    with pytest.raises(m.FencingError):
        m.fence(provider, 'old', INVENTORY, 'new', INCIDENT)
    assert provider.request.call_count == 1

def test_stop_acceptance_is_not_proof_of_fencing():
    provider = Mock()
    provider.request.side_effect = [status(), {'ok': True, 'id': 'srv-1'}, status(), status()]
    with pytest.raises(m.FencingError, match='promotion is forbidden'):
        m.fence(provider, 'old', INVENTORY, 'new', INCIDENT, attempts=2, sleep=lambda _: None)

def test_power_off_requires_readback_of_correct_vm():
    provider = Mock()
    provider.request.side_effect = [status(), {'ok': True, 'id': 'srv-1'}, status(), status('stopped')]
    m.fence(provider, 'old', INVENTORY, 'new', INCIDENT, sleep=lambda _: None)
    assert provider.request.call_args_list[1].args == ('srv-1', 'stop', INCIDENT)

def test_already_stopped_never_sends_a_mutation():
    provider = Mock()
    provider.request.return_value = status('stopped')
    m.fence(provider, 'old', INVENTORY, 'new', INCIDENT)
    provider.request.assert_called_once_with('srv-1', 'status')

@pytest.mark.parametrize('target', ['new', 'missing'])
def test_cannot_stop_self_or_unknown_server(target):
    provider = Mock()
    with pytest.raises(m.FencingError):
        m.fence(provider, target, INVENTORY, 'new', INCIDENT)
    provider.request.assert_not_called()

def test_unavailable_provider_is_not_success():
    provider = Mock()
    provider.request.side_effect = m.FencingError('unavailable')
    with pytest.raises(m.FencingError):
        m.fence(provider, 'old', INVENTORY, 'new', INCIDENT)
