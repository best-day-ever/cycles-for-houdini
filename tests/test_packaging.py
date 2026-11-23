import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


tools = Path(__file__).resolve().parents[1] / 'tools'
spec = importlib.util.spec_from_file_location('packaging_bootstrap', tools / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
dist_spec = importlib.util.spec_from_file_location('packaging_dist', tools / 'make_dist.py')
dist = importlib.util.module_from_spec(dist_spec)
with patch.dict('sys.modules', bootstrap=bootstrap):
    dist_spec.loader.exec_module(dist)


class PackagingTests(unittest.TestCase):
    def test_solaris_assets_and_cook_module_ship_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            houdini = Path(temporary)
            otls = houdini / 'otls'
            otls.mkdir()
            (otls / 'hdcycles_render_products.hda').touch()
            with self.assertRaises(ValueError):
                dist.verify_solaris_payload(houdini)
            (otls / 'hdcycles_cryptomatte.hda').touch()
            with self.assertRaises(ValueError):
                dist.verify_solaris_payload(houdini)
            module = houdini / 'python3.13libs/cycles_solaris.py'
            module.parent.mkdir()
            module.touch()
            dist.verify_solaris_payload(houdini)

    def test_unresolved_dependency_pointer_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lib').mkdir()
            (root / 'lib/libsycl.so').write_text(
                'version https://git-lfs.github.com/spec/v1\n'
                'oid sha256:' + 'a' * 64 + '\nsize 123456\n')
            with self.assertRaisesRegex(ValueError, 'Unresolved Git LFS'):
                bootstrap.verify_payload_files(root)

    def test_materialized_dependency_and_relative_link_are_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lib.so.1').write_bytes(b'\x7fELF' + b'\0' * 64)
            (root / 'lib.so').symlink_to('lib.so.1')
            bootstrap.verify_payload_files(root)


if __name__ == '__main__':
    unittest.main()
