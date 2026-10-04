"""Small invariants that protect this project's single-worker production model."""
from pathlib import Path
import re
import yaml

for path in Path('.github/workflows').glob('*.yml'):
    text = path.read_text()
    assert 'pull_request_target' not in text, path
    assert 'self-hosted' not in text, path
    assert 'contents: read' in text, path
    for action in re.findall(r'uses:\s*([^\s#]+)', text):
        assert re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', action), (path, action)
resources = list(yaml.safe_load_all(Path('infra/kubernetes/application.yaml').read_text()))
for obj in resources:
    if obj.get('kind') == 'Deployment':
        spec = obj['spec']
        assert spec['replicas'] == 0, 'Future Kubernetes setup must not start a second bot'
        assert spec['strategy']['type'] == 'Recreate'
        pod = spec['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        for container in pod['containers']:
            assert container['securityContext']['readOnlyRootFilesystem']
            assert container['securityContext']['capabilities']['drop'] == ['ALL']
assert 'BILLING_ENABLED=0' in Path('infra/kubernetes/kustomization.yaml').read_text()
print('Workflow and future-cluster safety invariants: OK')
