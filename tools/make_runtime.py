"""Package a native standalone build, with no Houdini installation dependency."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

from bootstrap import VERSIONS, library_platform, patch_identity


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    name = 'cycles-usd-' + args.release + '-' + library_platform()
    staging = args.output_dir / name
    if staging.exists():
        raise ValueError('Use a fresh output directory: ' + str(staging))
    for name_required in ('cycles', 'cycles_image_info'):
        if not (args.install_dir / name_required).is_file():
            raise ValueError('Missing native executable: ' + name_required)
    shutil.copytree(args.install_dir, staging, symlinks=True)
    if any(staging.rglob('libpxr_*')) or any(staging.rglob('libHoudini*')):
        raise ValueError('SideFX libraries must never enter a standalone runtime')
    shutil.copy2(ROOT / 'runtime/render_usd.py', staging / 'render_usd.py')
    launcher = staging / 'cycles-usd-render'
    launcher.write_text('#!/bin/sh\nset -eu\n'
                        'root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'exec env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 "$root/render_usd.py" "$@"\n')
    launcher.chmod(0o755)
    notices = staging / 'notices'
    notices.mkdir(exist_ok=True)
    for name_notice in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / name_notice, notices / name_notice)
    dependencies = args.source_dir / 'lib' / library_platform()
    for package in dependencies.iterdir():
        if package.is_dir():
            for directory in ('license', 'licenses'):
                source = package / directory
                if source.is_dir():
                    shutil.copytree(source, notices / package.name / directory, symlinks=True)
    metadata = dict(release=args.release, cycles_base=VERSIONS['5.3']['commit'],
                    patch_sha256=patch_identity('5.3'), platform=platform.system(),
                    architecture=platform.machine(), standalone=True)
    (staging / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n')
    archive = args.output_dir / (name + '.tar.gz')
    # The archive unpacks directly into an immutable, administrator-selected prefix.
    with tarfile.open(archive, 'w:gz', compresslevel=1) as output:
        for path in staging.iterdir():
            output.add(path, arcname=path.name)
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    receipt = dict(metadata, archive=archive.name, sha256=digest.hexdigest(), bytes=archive.stat().st_size)
    archive.with_suffix('.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
