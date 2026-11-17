#!/usr/bin/env python3
"""Render an exported USD sequence without Houdini; publish only decoded images."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile


FRAME_TOKEN = re.compile(r"#+|\$F\d*|%0?\d*d")


def frame_path(pattern, frame):
    def expand(match):
        token = match.group()
        width = len(token) if token.startswith('#') else int(re.sub(r'\D', '', token) or 0)
        return f'{frame:0{width}d}'
    return FRAME_TOKEN.sub(expand, str(pattern))


def run_backend(command, env):
    process = subprocess.Popen(command, env=env, start_new_session=True)
    def cancel(signum, _frame):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = process.wait()
        if result:
            raise subprocess.CalledProcessError(result, command)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def validate_image(path, runtime, env):
    result = subprocess.run([str(runtime / 'cycles_image_info'), str(path)],
                            env=env, check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    if (info['width'] <= 0 or info['height'] <= 0 or info['channels'] < 3
            or info['nonfinite'] != 0):
        raise RuntimeError('Cycles wrote invalid pixels: ' + str(path))
    return info


def render_frames(args, runtime):
    if args.frame_end < args.frame_start or args.frame_step < 1 or args.threads < 0 or args.samples < 0:
        raise ValueError('Invalid frame range, samples or thread count')
    if args.frame_start != args.frame_end and not FRAME_TOKEN.search(args.output):
        raise ValueError('An image sequence requires ####, $F4 or %04d in its output path')
    if Path(args.output).suffix.lower() != '.exr':
        raise ValueError('Choose an EXR output path')
    env = dict(os.environ)
    # No SideFX library path, Python module or renderer plugin enters this process.
    for key in ('HFS', 'HH', 'PYTHONHOME', 'PYTHONPATH', 'PXR_PLUGINPATH_NAME',
                'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'):
        env.pop(key, None)
    env['CYCLES_MATERIALX_LIBRARIES'] = str(runtime / 'share/materialx')
    for frame in range(args.frame_start, args.frame_end + 1, args.frame_step):
        scene = Path(frame_path(args.scene, frame)).resolve()
        if not scene.is_file():
            raise ValueError('USD scene does not exist: ' + str(scene))
        destination = Path(frame_path(args.output, frame)).absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.' + destination.stem + '.cycles-',
                                                 suffix='.exr', dir=destination.parent)
        os.close(descriptor)
        scratch = Path(temporary)
        try:
            command = [str(runtime / 'cycles'), '--background', '--device', args.device,
                       '--frame', str(frame), '--output', str(scratch), '--threads', str(args.threads)]
            if args.samples:
                command += ['--samples', str(args.samples)]
            if args.camera:
                command += ['--camera', args.camera]
            if args.settings:
                command += ['--render-settings', args.settings]
            if args.resolution:
                command += ['--width', str(args.resolution[0]), '--height', str(args.resolution[1])]
            command.append(str(scene))
            run_backend(command, env)
            info = validate_image(scratch, runtime, env)
            os.replace(scratch, destination)
            print(json.dumps(dict(frame=frame, output=str(destination), image=info)), flush=True)
        finally:
            scratch.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', nargs='?')
    parser.add_argument('--output')
    parser.add_argument('--frame-start', type=int, default=1)
    parser.add_argument('--frame-end', type=int, default=1)
    parser.add_argument('--frame-step', type=int, default=1)
    parser.add_argument('--camera')
    parser.add_argument('--settings')
    parser.add_argument('--device', choices=('GPU', 'CPU', 'OPTIX', 'CUDA', 'METAL'), default='GPU')
    parser.add_argument('--samples', type=int, default=0)
    parser.add_argument('--threads', type=int, default=0)
    parser.add_argument('--resolution', type=int, nargs=2)
    parser.add_argument('--version', action='store_true')
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parent
    if args.version:
        print('Cycles ' + json.loads((runtime / 'release.json').read_text())['release'])
        return
    if not args.scene or not args.output:
        parser.error('scene and --output are required')
    try:
        render_frames(args, runtime)
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
