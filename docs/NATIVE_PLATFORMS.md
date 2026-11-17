# Native Linux, Apple Silicon and standalone USD

This fork adds native platform builds and a standalone USD sequence renderer to
the upstream Houdini integration. Houdini clients and standalone runtimes are
separate builds: each Houdini client uses its SDK's USD/Python/image libraries;
the standalone renderer uses Blender's pinned open-source dependencies.

## Builds

Install CMake, a native C++ compiler, Git and Git LFS. The bootstrap selects the
source-pinned `linux_x64`, `linux_arm64`, `macos_arm64` or Windows dependency
submodule. Apple Silicon enables Metal. Linux/Windows optionally build CUDA and
OptiX. Use a separate build and install directory for every SDK.

```sh
# Native Apple Silicon standalone runtime, SVM shaders and Metal.
python3 tools/bootstrap.py --standalone --no-osl --jobs 2 \
  --build-dir /tmp/cycles-build/standalone --install-dir /tmp/cycles-install/standalone

# Native H22 client. HOUDINI_ROOT on macOS is the directory containing
# Frameworks/Houdini.framework, rather than its Resources directory.
python3 tools/bootstrap.py --no-osl --jobs 2 --houdini "$HOUDINI_ROOT" \
  --build-dir /tmp/cycles-build/h22 --install-dir /tmp/cycles-install/h22

# Run with the exact Houdini's hython and the new plugin registered.
HOUDINI_PATH='/tmp/cycles-install/h22/houdini:&' \
PXR_PLUGINPATH_NAME=/tmp/cycles-install/h22/houdini/dso/usd_plugins \
CYCLES_INSTALL_DIR=/tmp/cycles-install/h22 hython tools/build_cycles_vops.py
python3 tools/make_dist.py --install-dir /tmp/cycles-install/h22 \
  --name CyclesForHoudini-5.3-h22-macos-arm64 --zip
```

For NVIDIA, provide the OptiX SDK with `--optix "$OPTIX_ROOT_DIR"` and a CUDA
toolkit using `--cmake-arg=-DCUDA_TOOLKIT_ROOT_DIR="$CUDA_ROOT"`. CUDA12.9 builds
native Ada (`sm_89`, RTX40) and Blackwell (`sm_120`, RTX50/RTX PRO Blackwell)
kernels. Keep `compute_75` PTX for driver compilation on other compatible
architectures. `--cuda-arch` accepts a semicolon-separated matrix. Check the
configure log for every requested architecture; an older toolkit may skip an
unsupported architecture. The build defaults to two jobs, serial CUDA
architectures and bounded nvcc split compilation.

Choose an OptiX SDK compatible with the installed driver. For an R580 driver,
use OptiX9.0 (R570 or newer); OptiX9.1 requires R590 or newer. This lets the
native Ada/Blackwell build retain the workstation's existing driver.

Existing source caches require a completed patch identity receipt. When the
patch series changes, prepare a new source cache with `--source-dir`; preserve
the old cache for its existing build. Dependencies stay pinned by the Cycles
Git submodule revisions and LFS hashes.

## Standalone sequences

Package the independent install:

```sh
python3 tools/make_runtime.py --release 5.3.0-bde1 \
  --install-dir /tmp/cycles-install/standalone \
  --source-dir external/cycles-53 --output-dir /tmp/cycles-dist
```

After unpacking, the renderer uses system Python's standard library and its own
native Cycles/USD/image libraries:

```sh
./cycles-usd-render /show/shot/scene.usd --device GPU \
  --frame-start 1001 --frame-end 1010 --output '/show/shot/render/beauty.$F4.exr'
```

`GPU` selects an available NVIDIA OptiX/CUDA or Apple Metal device. Explicit
`CPU`, `OPTIX`, `CUDA` and `METAL` selections are available. An unavailable GPU
or a failed GPU render fails the command. Outputs are decoded and checked for
finite pixels before an atomic replacement of the destination image.

Input filenames can also contain `####`, `$F4` or `%04d` for per-frame USD
exports. A single USD can carry time samples. `--camera` and `--settings` select
USD prim paths; otherwise the stage's render settings select the camera and
resolution. `--samples 0` uses authored settings. `--resolution WIDTH HEIGHT`
and a positive `--samples` override the stage. The native `cycles` binary also
accepts `--frame` (including fractional time codes), `--camera` and
`--render-settings`.

The standalone command currently publishes combined RGBA beauty EXRs. The
Houdini delegate retains the upstream render-var/AOV integration. Export
textures and referenced layers to accessible filesystem paths. Use the Cook COP
Textures to Files output processor for COP textures; an exported `op:` path has
no Houdini graph on a standalone worker. Custom asset resolvers require a client
built against the standalone runtime's own USD ABI.

## Qualification

Run the behavior tests with `python3 -m unittest discover -s tests -p 'test_*.py'`.
They cover platform/backend selection, sequence tokens, command boundaries,
failure propagation and atomic publication. Native builds and actual rendered
pixels are separate acceptance gates.

`hython tools/qualify_houdini.py --output-dir /tmp/cycles-qa --require-plugin`
checks plugin discovery, the native generated VOP and a Solaris LOP export. Its
two frames change a native Cycles material from red to green and move a
MaterialX object. An intentionally incorrect first camera checks selection of
the authored render camera; sphere/cube primitives exercise implicit-surface
conversion. Use fresh Houdini preferences for qualification.

For headless Husk, select the backend through `CYCLES_DEVICE=METAL` or
`CYCLES_DEVICE=OPTIX`, and use `--renderer HdCyclesPluginGPU`. Husk's `--gpu` flag
enables its graphics context; an SSH-only macOS session may fail creating that
context. The Cycles output driver supports headless GPU rendering.

The initial native Apple Silicon acceptance rendered the same H22.0.466-exported
USD on CPU and the M2 Ultra's 76-core Metal GPU. Both 96×64 two-frame sequences
contained finite, nonblack pixels and changed with USD time. Mean absolute
CPU/Metal pixel difference was 2.1×10⁻⁸; frame-to-frame change was 0.08543.
Native H22 plugin discovery, generation of 102 VOPs, Solaris authoring and
two-frame Metal Husk rendering also pass (image change 0.08540).
The packaged runtime also renders after relocation on an M2 Ultra and an M4
Max (40-core GPU). Both animated sequences have finite, nonblack pixels;
frame-to-frame change is 0.08540 and their mean pixel difference is 1.7×10⁻⁸.
The relocated H22 package passes the same two-frame Metal Husk render.
Expanded NVIDIA acceptance is recorded with each release; compiled kernels
alone do not qualify a hardware generation.
