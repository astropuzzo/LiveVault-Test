#!/usr/bin/env python3
"""Require operations documentation alongside changes to runtime/deployment."""
import subprocess
import sys

base = sys.argv[1] if len(sys.argv) > 1 else 'HEAD^'
if not base or set(base) == {'0'}:
    base = 'HEAD^'
changed = subprocess.check_output(['git', 'diff', '--name-only', base, 'HEAD'], text=True).splitlines()
runtime = [p for p in changed if p.startswith(('app/', 'control-panel/', 'nina-monitor/', 'scripts/', '.github/workflows/'))
           and not p.endswith('.md')]
runtime += [p for p in changed if p in {'Dockerfile', 'requirements.txt', 'docker-compose.yml', '.dockerignore'}]
documented = any(p in {'AI-HANDOFF.md', 'HOSTING.md', 'control-panel/README.md', 'nina-monitor/COOLIFY.md'}
                 or (p.startswith('docs/') and p.endswith('.md')) for p in changed)
if runtime and not documented:
    sys.exit('Operational changes require an updated operations document in the same change; read AGENTS.md.')
print('Operations documentation check passed.')
