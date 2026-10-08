"""VPSPay power fencing primitive; not an automatic promotion controller.

The caller must hold the cluster leader lock and select the former leader from
trusted inventory. A successful stop request alone never authorizes promotion.
This module is deliberately not installed as a timer or Patroni hook yet.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.request


class FencingError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise FencingError('Provider redirects are forbidden')


class Provider:
    def __init__(self, token):
        self._token = token.strip()
        if not self._token:
            raise ValueError('Missing provider credential')
        self._opener = urllib.request.build_opener(NoRedirect)

    def request(self, server_id, action, incident_id=None):
        if not re.fullmatch(r'srv-[0-9]+', server_id) or action not in ('status', 'stop'):
            raise FencingError('Unsupported provider operation')
        method = 'POST' if action == 'stop' else 'GET'
        if method == 'POST' and (not isinstance(incident_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', incident_id)):
            raise FencingError('A stable incident id is required')
        path = '/api/personal/v1/servers/' + server_id + '/' + action
        stamp, nonce = str(int(time.time())), secrets.token_hex(24)
        canonical = '\n'.join([method, path, stamp, nonce, hashlib.sha256(b'').hexdigest()])
        signature = hmac.new(hashlib.sha256(self._token.encode()).digest(), canonical.encode(), hashlib.sha256).hexdigest()
        headers = {'Authorization': 'Bearer ' + self._token,
                   'X-Timestamp': stamp, 'X-Nonce': nonce, 'X-Signature': signature,
                   'Accept': 'application/json', 'User-Agent': 'NeuroPost-Operations/1.0'}
        if method == 'POST':
            headers['Idempotency-Key'] = incident_id
        request = urllib.request.Request('https://apibot.vpspay.net' + path,
                                         data=b'' if method == 'POST' else None,
                                         headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=10) as response:
                result = json.loads(response.read(65537))
        except (OSError, ValueError) as error:
            # Never include response bodies or request headers in logs.
            raise FencingError('Provider request failed; fencing is unconfirmed') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise FencingError('Provider did not confirm the request')
        return result


def validate_status(status, server_id, panel_id):
    if (not isinstance(status, dict) or status.get('ok') is not True
            or status.get('id') != server_id or status.get('panel_server_id') != panel_id
            or status.get('live_data_available') is not True
            or status.get('state') not in ('active', 'stopped')
            or status.get('status') != status.get('state')):
        raise FencingError('Unknown, stale or mismatched power state')
    return status['state']


def fence(provider, target, inventory, local_name, incident_id, *, attempts=12, sleep=time.sleep):
    """Return only after a live status confirms the exact former VM is stopped.

    Inventory maps cluster names to stable API/panel identities. Loss of access,
    deleted servers, rental suspension, 404 and asynchronous stop acceptance
    are deliberately NOT evidence of power fencing.
    """
    if target == local_name or local_name not in inventory or target not in inventory:
        raise FencingError('Refusing an unknown target or self-fencing')
    ids = [entry['server_id'] for entry in inventory.values()]
    panels = [entry['panel_id'] for entry in inventory.values()]
    if len(set(ids)) != len(ids) or len(set(panels)) != len(panels):
        raise FencingError('Ambiguous server inventory')
    if not isinstance(incident_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', incident_id):
        raise FencingError('Invalid incident id')
    if not 1 <= attempts <= 12:
        raise FencingError('Invalid retry limit')
    server_id, panel_id = inventory[target]['server_id'], inventory[target]['panel_id']
    def state():
        return validate_status(provider.request(server_id, 'status'), server_id, panel_id)
    if state() == 'stopped':
        return
    result = provider.request(server_id, 'stop', incident_id)
    if result.get('ok') is not True or result.get('id') != server_id:
        raise FencingError('Stop acknowledgement does not match the target')
    for attempt in range(attempts):
        if state() == 'stopped':
            return
        if attempt + 1 < attempts:
            sleep(5)
    raise FencingError('Power-off deadline exceeded; promotion is forbidden')
