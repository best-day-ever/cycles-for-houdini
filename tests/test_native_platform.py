import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'tools/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class NativePlatformTests(unittest.TestCase):
    def test_native_dependency_selection(self):
        for system, arch, expected in [('Linux', 'x86_64', 'linux_x64'),
                                      ('Linux', 'aarch64', 'linux_arm64'),
                                      ('Windows', 'AMD64', 'windows_x64'),
                                      ('Darwin', 'arm64', 'macos_arm64')]:
            with self.subTest(system=system, arch=arch):
                self.assertEqual(bootstrap.library_platform(system, arch), expected)

    def test_unsupported_platform_fails_before_downloading(self):
        for system, arch in [('Darwin', 'x86_64'), ('Linux', 'riscv64')]:
            with self.assertRaises(ValueError):
                bootstrap.library_platform(system, arch)

    def configured(self, target, arch, optix=''):
        with mock.patch.object(bootstrap, 'run') as run:
            bootstrap.configure('/source', {'install': 'unused'}, '', optix, arch, False,
                                target=target, build='/build', install='/runtime')
        return run.call_args.args[0]

    def test_metal_build_does_not_request_nvidia_kernels(self):
        args = self.configured('macos_arm64', bootstrap.CUDA_ARCH, '/optix')
        self.assertIn('-DWITH_CYCLES_DEVICE_METAL=ON', args)
        self.assertNotIn('-DWITH_CYCLES_DEVICE_OPTIX=ON', args)
        self.assertNotIn('-DWITH_CYCLES_CUDA_BINARIES=ON', args)

    def test_ada_and_blackwell_are_built_with_ptx_fallback(self):
        args = self.configured('linux_x64', bootstrap.CUDA_ARCH, '/optix')
        arch = next(a.split('=', 1)[1] for a in args if a.startswith('-DCYCLES_CUDA_BINARIES_ARCH='))
        self.assertTrue({'sm_89', 'sm_120', 'compute_75'} <= set(arch.split(';')))
        self.assertIn('-DWITH_CYCLES_DEVICE_OPTIX=ON', args)
        self.assertIn('-DWITH_CYCLES_CUDA_BUILD_SERIAL=ON', args)
        self.assertNotIn('-DCUDA_NVCC_FLAGS=--split-compile=0', args)

    def test_cpu_build_clears_old_cuda_settings(self):
        self.assertIn('-DWITH_CYCLES_CUDA_BINARIES=OFF', self.configured('linux_x64', ''))


if __name__ == '__main__':
    unittest.main()
