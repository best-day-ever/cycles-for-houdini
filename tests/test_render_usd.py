import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_usd', ROOT / 'runtime/render_usd.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderTests(unittest.TestCase):
    def test_frame_tokens_and_negative_times(self):
        for token in ('####', '$F4', '%04d'):
            self.assertEqual(renderer.frame_path('image.' + token + '.exr', -2), 'image.-002.exr')
        self.assertEqual(renderer.frame_path('image.$F.exr', 1001), 'image.1001.exr')

    @contextlib.contextmanager
    def runtime(self, result=0, pixels=b'valid'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'cycles'
            binary.write_text('#!' + sys.executable + '\n' +
                'import sys,pathlib,json,os\na=sys.argv\n' +
                "pathlib.Path(a[a.index('--output')+1]).write_bytes(" + repr(pixels) + ')\n' +
                "pathlib.Path(__file__).with_name('request.json').write_text(json.dumps(dict(args=a,hfs=os.environ.get('HFS'))))\n" +
                'sys.exit(' + str(result) + ')\n')
            binary.chmod(0o755)
            inspector = root / 'cycles_image_info'
            inspector.write_text('#!' + sys.executable + '\n' +
                'import json,sys,pathlib\n' +
                "assert pathlib.Path(sys.argv[1]).read_bytes()==b'valid'\n" +
                'print(json.dumps(dict(width=64,height=64,channels=4,nonfinite=0)))\n')
            inspector.chmod(0o755)
            scene = root / 'a scene;literal.usda'
            scene.write_text('#usda 1.0')
            args = argparse.Namespace(scene=str(scene), output=str(root / 'out.$F4.exr'),
                frame_start=1, frame_end=2, frame_step=1, threads=2, samples=4,
                camera='/World/camera', settings='/Render/settings', device='METAL', resolution=None)
            yield root, args

    def test_sequence_publishes_complete_images_and_keeps_literal_paths(self):
        with self.runtime() as (root, args), mock.patch.dict(os.environ, HFS='/a/sidefx/sdk'):
            with contextlib.redirect_stdout(io.StringIO()):
                renderer.render_frames(args, root)
            self.assertEqual((root / 'out.0001.exr').read_bytes(), b'valid')
            self.assertEqual((root / 'out.0002.exr').read_bytes(), b'valid')
            request = json.loads((root / 'request.json').read_text())
            self.assertEqual(Path(request['args'][-1]), Path(args.scene).resolve())
            self.assertIsNone(request['hfs'])
            self.assertFalse(list(root.glob('.*.cycles-*')))

    def test_failed_render_preserves_previous_output_and_removes_partial(self):
        with self.runtime(result=7) as (root, args):
            previous = root / 'out.0001.exr'
            previous.write_bytes(b'previous')
            with self.assertRaises(subprocess.CalledProcessError):
                renderer.render_frames(args, root)
            self.assertEqual(previous.read_bytes(), b'previous')
            self.assertFalse(list(root.glob('.*.cycles-*')))

    def test_successful_process_with_bad_image_is_rejected(self):
        with self.runtime(pixels=b'partial') as (root, args):
            with self.assertRaises(subprocess.CalledProcessError):
                renderer.render_frames(args, root)
            self.assertFalse((root / 'out.0001.exr').exists())
            self.assertFalse(list(root.glob('.*.cycles-*')))

    def test_sequence_cannot_overwrite_one_path(self):
        with self.runtime() as (root, args):
            args.output = str(root / 'single.exr')
            with self.assertRaises(ValueError):
                renderer.render_frames(args, root)
            self.assertFalse((root / 'request.json').exists())

    def test_input_file_sequences_select_the_current_frame(self):
        with self.runtime() as (root, args):
            for frame in (1, 2):
                (root / ('scene.%04d.usda' % frame)).write_text('#usda 1.0')
            args.scene = str(root / 'scene.$F4.usda')
            with contextlib.redirect_stdout(io.StringIO()):
                renderer.render_frames(args, root)
            request = json.loads((root / 'request.json').read_text())
            self.assertEqual(Path(request['args'][-1]), (root / 'scene.0002.usda').resolve())

    def test_invalid_resolution_stops_before_starting_a_renderer(self):
        with self.runtime() as (root, args):
            args.resolution = [-1, 64]
            with self.assertRaises(ValueError):
                renderer.render_frames(args, root)
            self.assertFalse((root / 'request.json').exists())


if __name__ == '__main__':
    unittest.main()
