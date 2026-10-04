#!/usr/bin/env python3
"""Apply repository settings after CI has been published and verified.

Uses an already authenticated GitHub CLI. No token is printed or stored here.
Run baseline first; protect-main only after the current main's CI succeeds.
"""
import argparse
import json
import subprocess

REPOSITORY = 'IzmestevIvan/Neyro_posting'


def api(method, path, body=None):
    command = ['gh', 'api', '--method', method, f'repos/{REPOSITORY}/{path}'.rstrip('/')]
    if body is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def baseline():
    # Idempotent settings; this does not grant secrets to PR jobs.
    api('PUT', 'vulnerability-alerts')
    api('PUT', 'automated-security-fixes')
    api('PUT', 'private-vulnerability-reporting')
    api('PATCH', '', {
        'delete_branch_on_merge': True,
        'allow_squash_merge': True,
        'security_and_analysis': {
            'secret_scanning': {'status':'enabled'},
            'secret_scanning_push_protection': {'status':'enabled'},
        },
    })
    api('PUT', 'actions/permissions/workflow', {
        'default_workflow_permissions':'read', 'can_approve_pull_request_reviews':False,
    })
    api('PUT', 'environments/production', {
        'wait_timer':0,
        'deployment_branch_policy':{'protected_branches':True, 'custom_branch_policies':False},
    })
    print('Repository security settings and production environment configured.')


def protect_main():
    sha = api('GET','git/ref/heads/main')['object']['sha']
    runs = api('GET',f'actions/workflows/ci.yml/runs?head_sha={sha}&branch=main&event=push')['workflow_runs']
    if not runs or runs[0]['head_sha'] != sha or runs[0]['conclusion'] != 'success':
        raise SystemExit('Publish and pass CI on main before enabling required checks')
    api('PUT','branches/main/protection',{
        'required_status_checks': {'strict':True,'contexts':['Backend','Frontend','Infrastructure','Container']},
        'enforce_admins':True,
        # Solo owner can merge after CI; own PR cannot satisfy a required approval.
        'required_pull_request_reviews': {'dismiss_stale_reviews':True,'required_approving_review_count':0},
        'restrictions':None,
        'required_conversation_resolution':True,
        'allow_force_pushes':False,
        'allow_deletions':False,
    })
    print('Main now requires PRs and the four CI checks; force push/deletion disabled.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['baseline','protect-main'])
    args=parser.parse_args()
    try:
        baseline() if args.phase=='baseline' else protect_main()
    except FileNotFoundError:
        raise SystemExit('Install and authenticate GitHub CLI first: gh auth login')
    except subprocess.CalledProcessError as exc:
        # gh errors contain only API diagnostics, but avoid printing request bodies.
        raise SystemExit(f'GitHub request failed (exit {exc.returncode}); check account permissions. Completed earlier operations remain applied.')
