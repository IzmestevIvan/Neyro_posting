"""Gate production on the immutable commit checked by the CI workflow."""
import json
import os
import re
import urllib.request


def get(path):
    request = urllib.request.Request('https://api.github.com/'+path, headers={
        'Authorization': 'Bearer '+os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    sha = os.environ['RELEASE_SHA']
    repository = os.environ['REPOSITORY']
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
        raise SystemExit('Invalid release identity')
    if get(f'repos/{repository}/git/ref/heads/main')['object']['sha'] != sha:
        raise SystemExit('Release must be the current main commit; revert through a reviewed commit to roll back')
    runs = get(f'repos/{repository}/actions/workflows/ci.yml/runs?head_sha={sha}&branch=main&event=push')['workflow_runs']
    if not runs or runs[0]['head_sha'] != sha or runs[0]['conclusion'] != 'success':
        raise SystemExit('A successful main CI run for this exact commit is required')
    print('Verified main and successful CI:', sha)


if __name__ == '__main__':
    main()
