#!/usr/bin/env python3
"""Release text for a tag: its section of CHANGELOG.md, then .forgejo/release-notes.md.
    tools/relnotes.py v0.65.0 > notes.md
A tag with no section (a v0.0.0-test pre-release) gets the install text alone; make bump
refuses a real release without one."""
import os, re, sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ver = sys.argv[1].lstrip('v')
log = open(os.path.join(root, 'CHANGELOG.md')).read()
m = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(ver), log, re.M | re.S)
notes = open(os.path.join(root, '.forgejo', 'release-notes.md')).read().replace('{ver}', ver)
if m and m.group(1).strip():
    notes = "## What's new in %s\n\n%s\n\n---\n\n%s" % (ver, m.group(1).strip(), notes)
sys.stdout.write(notes)
