"""Load a packaged Mach-O delegate after its original dependency is removed."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from make_dist import relocate_macos_delegate


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('cc'), 'native Mach-O compiler fixture')
class MacLoaderTests(unittest.TestCase):
    def test_relocated_delegate_uses_its_bundled_library(self):
        with tempfile.TemporaryDirectory(prefix='cycles-macho-') as temporary:
            root = Path(temporary)
            original = root / 'builder-dependencies'
            original.mkdir()
            (root / 'driver.c').write_text('int driver(void) { return 42; }\n')
            (root / 'module.c').write_text('extern int driver(void); int probe(void) { return driver(); }\n')
            (root / 'main.c').write_text('''#include <dlfcn.h>
int main(int argc, char **argv) {
  void *module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!module) return 3;
  int (*probe)(void) = (int (*)(void))dlsym(module, "probe");
  return !probe || probe() != 42;
}
''')
            plugin = root / 'relocated'
            delegate = plugin / 'houdini/dso/usd/hdCycles.dylib'
            delegate.parent.mkdir(parents=True)
            library = plugin / 'houdini/lib'
            library.mkdir()
            subprocess.run(['cc', '-dynamiclib', str(root / 'driver.c'),
                '-Wl,-install_name,@rpath/libdriver.dylib', '-o', str(original / 'libdriver.dylib')], check=True)
            subprocess.run(['cc', '-dynamiclib', str(root / 'module.c'), '-L' + str(original), '-ldriver',
                '-Wl,-rpath,' + str(original), '-Wl,-install_name,@rpath/hdCycles.dylib',
                '-o', str(delegate)], check=True)
            subprocess.run(['cc', str(root / 'main.c'), '-o', str(root / 'probe')], check=True)
            shutil.copy2(original / 'libdriver.dylib', library)
            relocate_macos_delegate(str(plugin))
            shutil.rmtree(original)
            subprocess.run([str(root / 'probe'), str(delegate)], check=True)
