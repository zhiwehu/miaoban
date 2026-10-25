#!/usr/bin/env python3
"""Fetch only pinned upstream Chinese/common prompt sounds, verifying every hash."""
import hashlib
import json
from pathlib import Path
import urllib.request


def main():
    here = Path(__file__).resolve().parent
    project = here.parent
    manifest = json.loads((here / 'public-sounds-manifest.json').read_text())
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe resource path')
        target = project / relative
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']:
                continue
            raise FileExistsError(f'Refusing to overwrite changed resource: {relative}')
        url = f"https://raw.githubusercontent.com/78/xiaozhi-esp32/{manifest['commit']}/{relative.as_posix()}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(entry['bytes'] + 1)
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError(f'Resource verification failed: {relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.ogg.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
    print(f"Verified {len(manifest['files'])} upstream prompt sounds")


if __name__ == '__main__':
    main()
