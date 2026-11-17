"""Exercise the ELF loader contract that previously crashed OIDN CUDA probing."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from make_dist import relocate_linux_delegate


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cmake') and shutil.which('cc'),
                     'native ELF compiler fixture')
class LinuxLoaderTests(unittest.TestCase):
    def test_driver_function_is_not_interposed_by_the_loader_pointer_table(self):
        with tempfile.TemporaryDirectory(prefix='cycles-loader-') as temporary:
            root = Path(temporary)
            (root / 'cuew.c').write_text('static int stub(int x) { return -1; }\n'
                                        'int (*cuInit)(int) = stub;\n')
            (root / 'driver.c').write_text('int cuInit(int x) { return 40; }\n')
            (root / 'sdk.c').write_text('int hgiSupport(void) { return 2; }\n')
            (root / 'module.c').write_text('extern int cuInit(int);\nextern int hgiSupport(void);\n'
                                          'int probe(void) { return cuInit(0) + hgiSupport(); }\n')
            (root / 'main.c').write_text('''#include <dlfcn.h>
extern int (*cuInit)(int);
int main(int argc, char **argv) {
  if (!cuInit) return 2;
  void *module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!module) return 3;
  int (*probe)(void) = (int (*)(void))dlsym(module, "probe");
  return !probe || probe() != 42;
}
''')
            (root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.25)
project(LoaderRegression C)
add_library(extern_cuew STATIC cuew.c)
add_library(driver SHARED driver.c)
add_library(sdk SHARED sdk.c)
add_library(module SHARED module.c)
target_link_libraries(module PRIVATE driver sdk)
set_target_properties(module PROPERTIES BUILD_RPATH
  "${CMAKE_CURRENT_BINARY_DIR}/original-very-long-native-houdini-sdk-prefix/dsolib")
add_executable(probe main.c)
set_target_properties(probe PROPERTIES ENABLE_EXPORTS ON)
target_link_libraries(probe PRIVATE extern_cuew dl)
''')
            build = root / 'build'
            subprocess.run(['cmake', '-S', str(root), '-B', str(build),
                '-DCMAKE_PROJECT_INCLUDE=' + str(ROOT / 'tools/native_linux.cmake')],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess.run(['cmake', '--build', str(build), '--parallel', '1'],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess.run([str(build / 'probe'), str(build / 'libmodule.so')], check=True)
            plugin = root / 'relocated'
            delegate_dir = plugin / 'houdini/dso/usd'
            delegate_dir.mkdir(parents=True)
            library_dir = plugin / 'houdini/lib'
            library_dir.mkdir()
            shutil.copy2(build / 'libmodule.so', delegate_dir / 'hdCycles.so')
            shutil.copy2(build / 'libdriver.so', library_dir)
            sdk = root / 'target-houdini'
            (sdk / 'dsolib').mkdir(parents=True)
            shutil.copy2(build / 'libsdk.so', sdk / 'dsolib')
            relocate_linux_delegate(str(plugin), str(sdk))
            # Remove the original dependency: only the relocated relative path works.
            (build / 'libdriver.so').unlink()
            (build / 'libsdk.so').unlink()
            subprocess.run([str(build / 'probe'), str(delegate_dir / 'hdCycles.so')], check=True)
