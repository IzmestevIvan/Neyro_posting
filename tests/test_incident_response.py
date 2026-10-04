"""Exercise recovery/fencing boundaries without touching live services or sending alerts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


monitor = load('ops_monitor', 'scripts/operations/monitor.py')
control = load('ops_control', 'scripts/operations/control.py')


class RecoveryPolicy(unittest.TestCase):
    def setUp(self):
        self.checks = dict.fromkeys(['database', 'disk', 'memory', 'security'], True)
        self.details = {'maintenance': False, 'containers': {'app': {'status': 'running', 'health': 'unhealthy'}}}

    def decision(self, previous=None, now=4000, role='primary', enabled=True):
        return monitor.recovery_decision(previous or {}, self.checks, self.details, now, role, enabled)

    def test_requires_three_consecutive_failures_then_applies_hourly_budget(self):
        state = {}
        for _ in range(2):
            state, action = self.decision(state)
            self.assertFalse(action)
        state, action = self.decision(state)
        self.assertTrue(action)
        state.update(attempts=[3900])
        self.assertFalse(self.decision(state)[1])
        state.update(attempts=[3000, 3900])
        self.assertFalse(self.decision(state, now=4801)[1])
        self.assertTrue(self.decision(state, now=6801)[1])

    def test_never_heals_reserve_or_disabled_policy(self):
        state = {'failures': 3}
        self.assertFalse(self.decision(state, role='reserve')[1])
        self.assertFalse(self.decision(state, enabled=False)[1])

    def test_unsafe_resources_database_security_or_maintenance_reset_counter(self):
        for key in self.checks:
            self.checks[key] = False
            state, action = self.decision({'failures': 3})
            self.assertEqual(state['failures'], 0)
            self.assertFalse(action)
            self.checks[key] = True
        self.details['maintenance'] = True
        self.assertFalse(self.decision({'failures': 3})[1])

    def test_stopped_unknown_starting_and_healthy_workers_never_restarted(self):
        for status, health in [('exited', 'unhealthy'), ('unknown', None), ('running', 'healthy'), ('running', 'starting'), ('restarting', 'unhealthy')]:
            self.details['containers']['app'] = {'status': status, 'health': health}
            self.assertFalse(self.decision({'failures': 3})[1])

    def test_corrupt_state_fails_closed(self):
        for state in [{'failures': '3'}, {'attempts': None}, {'attempts': [float('inf')]}, {'attempts': ['yesterday']}, {'attempts': [5000]}, {'failures': -1}]:
            self.assertFalse(self.decision(state)[1])

    def test_deploy_lock_prevents_recovery(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            with path.open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(monitor, 'DEPLOY_LOCK', path), patch.object(monitor, 'run') as run:
                    self.assertEqual(monitor.recover({'failures': 3}, {'role': 'primary'}), 'deployment_in_progress')
                    run.assert_not_called()

    def test_recovery_rechecks_and_persists_attempt_before_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'failures': 3, 'attempts': []}
            calls = []
            def execute(args, **kwargs):
                self.assertEqual(len(json.loads((root / 'recovery.json').read_text())['attempts']), 1)
                calls.append(args)
            with patch.object(monitor, 'STATE', root), patch.object(monitor, 'DEPLOY_LOCK', root / 'lock'), patch.object(monitor, 'observe', return_value=(self.checks, self.details)), patch.object(monitor, 'run', side_effect=execute):
                self.assertEqual(monitor.recover(state, {'role': 'primary', 'auto_recover': True}), 'app_restart_requested')
            self.assertEqual(calls, [['docker', 'restart', '--time', '30', 'neyro-app-1']])


class ControlledOperations(unittest.TestCase):
    def test_reserve_and_fencing_block_worker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(control, 'CONFIG', root), patch.object(control, 'LOCK', root / 'lock'), patch.object(control, 'run') as run:
                (root / 'config.json').write_text(json.dumps({'role': 'reserve'}))
                with self.assertRaises(RuntimeError):
                    control.apply('restart_app')
                (root / 'config.json').write_text(json.dumps({'role': 'primary'}))
                (root / 'FENCED').touch()
                for action in ('restart_app', 'maintenance_off'):
                    with self.assertRaises(RuntimeError):
                        control.apply(action)
                run.assert_not_called()

    def test_fencing_persists_before_stop_and_disables_docker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_text(json.dumps({'role': 'primary'}))
            commands = []
            def execute(args):
                self.assertTrue((root / 'FENCED').exists())
                commands.append(args)
                return 'false'
            with patch.object(control, 'CONFIG', root), patch.object(control, 'LOCK', root / 'lock'), patch.object(control, 'run', side_effect=execute):
                control.apply('fence_primary')
            self.assertEqual(commands[0], ['docker', 'update', '--restart=no', 'neyro-app-1', 'neyro-support-1'])
            self.assertFalse(any('neyro-db-1' in command for command in commands))

    def test_manual_restart_refuses_stopped_worker_and_unhealthy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_text(json.dumps({'role': 'primary'}))
            with patch.object(control, 'CONFIG', root), patch.object(control, 'LOCK', root / 'lock'):
                for outputs in [['false'], ['true', 'unhealthy']]:
                    with patch.object(control, 'run', side_effect=outputs):
                        with self.assertRaises(RuntimeError):
                            control.apply('restart_app')


if __name__ == '__main__':
    unittest.main()
