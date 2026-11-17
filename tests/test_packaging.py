import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('packaging_bootstrap',
    Path(__file__).resolve().parents[1] / 'tools/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class PackagingTests(unittest.TestCase):
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
