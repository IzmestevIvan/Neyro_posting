#!/usr/bin/env python3
"""Bounded read-only ingress exercise of the project's own production endpoint.

No login credentials, writes, Telegram calls or AI calls. This measures HTTP
admission and availability, not the capacity of the publishing pipeline.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shlex
import time

import httpx

TARGET = 'https://flipbazar.ru'
PRIMARY = '31.76.244.26'
RESERVE = '144.31.53.148'
PATHS = ('/', '/account', '/', '/healthz', '/api/bootstrap')
RATES = (5, 10, 25, 50, 100)

# No application environment, logs or private data are read by this observer.
OBSERVER = r'''
import json, pathlib, subprocess, time, urllib.request
role = ROLE
deadline = time.monotonic() + 240
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
previous = None
while time.monotonic() < deadline:
    started = time.monotonic()
    row = {'role': role, 'time': time.time()}
    try:
        values = list(map(int, pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        if previous:
            delta = [a-b for a,b in zip(values, previous)]
            row['cpu_percent'] = round(100 * (sum(delta)-delta[3]-delta[4]) / max(1,sum(delta)), 2)
        previous = values
        memory = dict(line.split(':',1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
        row['available_mib'] = int(memory['MemAvailable'].split()[0]) // 1024
        result = subprocess.run(['docker','inspect','--format','{{json .State}}','neyro-db-1'], capture_output=True,text=True,timeout=3,check=True)
        state = json.loads(result.stdout)
        row['db_healthy'] = state.get('Status') == 'running' and state.get('Health',{}).get('Status') == 'healthy'
        url = 'http://172.30.90.3:8080/healthz' if role == 'primary' else 'https://flipbazar.ru/healthz'
        probe = time.monotonic()
        with opener.open(url, timeout=3) as response:
            row['health_ok'] = response.status == 200 and json.load(response).get('ok') is True
        row['health_seconds'] = round(time.monotonic()-probe, 3)
    except Exception as exc:
        row['error_type'] = type(exc).__name__
        row['health_ok'] = False
    print(json.dumps(row),flush=True)
    time.sleep(max(0, 5-(time.monotonic()-started)))
'''


def unsafe(row):
    if not row.get('health_ok') or not row.get('db_healthy'):
        return 'health_or_database_failed'
    if row.get('available_mib', 0) < 256:
        return 'low_available_memory'
    return None


def percentile(values, fraction=.95):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered)*fraction)-1)], 4) if ordered else None


async def run_trial(output, seconds=20):
    if not 5 <= seconds <= 30:
        raise ValueError('Each stage must last between 5 and 30 seconds')
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'target': TARGET,
              'scope': 'read_only_http_single_client_ip', 'stages': [], 'observations': [],
              'stop_reason': None, 'channels_changed': False}
    stop = asyncio.Event()
    ready = {name: asyncio.Event() for name in ('primary', 'reserve')}
    last_seen = {}
    processes, watchers = [], []

    def abort(reason):
        if not stop.is_set():
            report['stop_reason'] = reason
            stop.set()
            print(json.dumps({'stopped': reason}), flush=True)

    async def watch(role, process):
        cpu_streak = latency_streak = 0
        while line := await process.stdout.readline():
            try:
                row = json.loads(line)
            except ValueError:
                abort(role + ':invalid_observer_output')
                return
            report['observations'].append(row)
            last_seen[role] = time.monotonic()
            ready[role].set()
            reason = unsafe(row)
            cpu_streak = cpu_streak + 1 if row.get('cpu_percent', 0) > 95 else 0
            latency_streak = latency_streak + 1 if row.get('health_seconds', 0) > 1.5 else 0
            if reason or cpu_streak >= 3 or latency_streak >= 2:
                abort(role + ':' + (reason or 'sustained_resource_or_latency_limit'))
        abort(role + ':observer_disconnected')

    async def watchdog():
        while not stop.is_set():
            if any(time.monotonic()-last_seen.get(role, 0) > 15 for role in ready):
                abort('observer_stale')
            await asyncio.sleep(1)

    try:
        for role, host in (('primary', PRIMARY), ('reserve', RESERVE)):
            code = OBSERVER.replace('role = ROLE', 'role = ' + repr(role))
            process = await asyncio.create_subprocess_exec(
                'ssh', '-i', str(Path.home()/'.ssh/neyro_deploy'), '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=5',
                '-o', 'ServerAliveCountMax=2', 'root@'+host,
                'python3 -u -c '+shlex.quote(code), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            processes.append(process)
            watchers.append(asyncio.create_task(watch(role, process)))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready.values())), 25)
        watchers.append(asyncio.create_task(watchdog()))
        limits = httpx.Limits(max_connections=24, max_keepalive_connections=24)
        async with httpx.AsyncClient(timeout=3, limits=limits, follow_redirects=False,
                                     headers={'User-Agent': 'NeuroPost-Owner-LoadCheck/1.0'}) as client:
            for rate in RATES:
                if stop.is_set():
                    break
                status, latencies, accepted, errors = Counter(), [], [], Counter()
                active = set()
                skipped = 0
                start = time.monotonic()

                async def request(index):
                    before = time.monotonic()
                    path = PATHS[index % len(PATHS)]
                    try:
                        response = await client.get(TARGET+path)
                        status[str(response.status_code)] += 1
                        duration = time.monotonic()-before
                        latencies.append(duration)
                        if response.status_code in (200, 401):
                            accepted.append(duration)
                        if response.status_code >= 500:
                            abort('http_5xx')
                    except httpx.HTTPError as exc:
                        errors[type(exc).__name__] += 1
                        if sum(errors.values()) >= 3:
                            abort('repeated_request_errors')

                for index in range(rate*seconds):
                    if stop.is_set():
                        break
                    delay = start + index/rate - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if stop.is_set():
                        break
                    if len(active) >= 24:
                        skipped += 1
                        continue
                    task = asyncio.create_task(request(index))
                    active.add(task)
                    task.add_done_callback(active.discard)
                await asyncio.gather(*active)
                elapsed = time.monotonic()-start
                row = {'target_rps': rate, 'duration_seconds': round(elapsed, 2),
                       'status_counts': dict(status), 'errors': dict(errors),
                       'completed_rps': round(sum(status.values())/elapsed, 2),
                       'p95_seconds': percentile(latencies),
                       'accepted_p95_seconds': percentile(accepted),
                       'generator_skipped': skipped}
                report['stages'].append(row)
                print(json.dumps(row), flush=True)
                if not stop.is_set():
                    await asyncio.sleep(5)
            # Observe normal traffic after removing the offered load.
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        abort('operator_cancelled')
        raise
    except (TimeoutError, OSError) as exc:
        abort(type(exc).__name__)
    finally:
        for task in watchers:
            task.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'report': str(output), 'stop_reason': report['stop_reason']}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly send the bounded live load')
    parser.add_argument('--output', type=Path, default=Path('data/load-trial/http-report.json'))
    args = parser.parse_args()
    if not args.run:
        print(f'Plan only: {TARGET}, {RATES} requests/sec for 20s each; maximum 24 in flight.')
    else:
        asyncio.run(run_trial(args.output))
