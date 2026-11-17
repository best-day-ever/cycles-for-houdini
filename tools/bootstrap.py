"""Clones Cycles at the pinned commit, applies our patches, builds, installs.

Cycles is not copied into this repository: our changes live there as a patch
series, in patches/. This tool redoes the whole path, from the clone to the
Houdini install ready to be dropped into a package.

    python tools/bootstrap.py --version 5.3

The clone is a full git repository, on a branch of our own: once in place, you
work in it normally, and `git format-patch` regenerates the series.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Each version targets a specific upstream commit. A patch series only applies
# cleanly on the base it was exported against.
VERSIONS = {
    "5.3": {
        "dir": "cycles-53",
        "install": "install-53",
        "branch": "houdini-fixes-53",
        "commit": "b3f6262640f1a2f2d10b55b6ffccbf310ce0f981",
    },
}

UPSTREAM = "https://projects.blender.org/blender/cycles.git"

# One cubin per generation: Maxwell sm_50/52, Pascal sm_60/61, Volta sm_70,
# Turing sm_75, Ampere sm_86, Ada sm_89, Blackwell sm_120. At run time Cycles
# takes the cubin of the same major version with the highest minor that does
# not exceed the card's (sm_89 runs the sm_86 one), and otherwise JIT-compiles
# the compute_75 PTX through the driver, which covers sm_80, sm_90, sm_100 and
# later. sm_100/sm_120 need CUDA 12.8+; Cycles skips an architecture its
# toolkit cannot build.
CUDA_ARCH = "sm_50;sm_52;sm_60;sm_61;sm_70;sm_75;sm_86;sm_89;sm_120;compute_75"


def library_platform(system=None, machine=None):
    """Select the native, source-pinned Blender dependency submodule."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64",
            "aarch64": "arm64"}.get(machine)
    os_name = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(system)
    if not arch or not os_name or (os_name == "macos" and arch != "arm64"):
        raise ValueError("unsupported native build platform: %s/%s" % (system, machine))
    return os_name + "_" + arch


def patch_identity(version):
    series = os.path.join(ROOT, "patches", version)
    digest = hashlib.sha256()
    for name in sorted(os.listdir(series)):
        if name.endswith(".patch"):
            digest.update(name.encode())
            with open(os.path.join(series, name), "rb") as patch:
                digest.update(patch.read())
    return digest.hexdigest()


def verify_payload_files(root):
    """A release must contain dependency bytes, never Git LFS pointer files."""
    for base, _, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as stream:
                if stream.read(64).startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                    raise ValueError("Unresolved Git LFS dependency in release: " + path)


def run(cmd, cwd=None, env=None):
    shown = cmd if len(cmd) < 20 else cmd[:4] + ["... (%d arguments)" % len(cmd)]
    print("  $ " + " ".join(shown), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def clone(src, cfg):
    """Treeless clone, then checkout of the pinned commit.

    Treeless rather than shallow: the history stays browsable and `git am` has
    enough to work with, without fetching every revision of every file.
    """
    run(["git", "clone", "--filter=tree:0", UPSTREAM, src])
    run(["git", "checkout", "-b", cfg["branch"], cfg["commit"]], cwd=src)
    # Blender's precompiled libraries, as a submodule + git-lfs. Blender's
    # .gitmodules declares them `update = none` so that a plain checkout does
    # not pull gigabytes: asked for explicitly, or `git submodule update`
    # prints "Skipping submodule" and the configure step fails for want of
    # every dependency (seen on the clean-clone check of 2026-10-08).


def prepare_libraries(src, target, osl=False):
    name = "lib/" + target
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
    run(["git", "-c", "submodule.%s.update=checkout" % name,
         "submodule", "update", "--init", "--depth", "1", name], cwd=src, env=env)
    run(["git", "lfs", "install", "--local"], cwd=os.path.join(src, name))
    # OSL/LLVM and oneAPI are not needed by the supported SVM backends. Avoid
    # downloading their multi-GB compiler distributions on every build host.
    run(["git", "lfs", "pull", "--include=", "--exclude=" +
         ("dpcpp/**" if osl else "dpcpp/**,llvm/**,osl/**")],
        cwd=os.path.join(src, name))
    if not target.startswith("macos_"):
        # Blender's OIDN/Embree libraries link the SYCL runtime even when the
        # Cycles oneAPI backend is disabled. Fetch runtime DSOs, not its compiler.
        run(["git", "lfs", "pull", "--include=dpcpp/lib/*.so*,dpcpp/bin/sycl*.dll,"
             "dpcpp/bin/ur_*.dll,dpcpp/bin/pi_*.dll", "--exclude="],
            cwd=os.path.join(src, name))


def apply_patches(src, version):
    series = os.path.join(ROOT, "patches", version)
    patches = sorted(
        os.path.join(series, f) for f in os.listdir(series) if f.endswith(".patch")
    )
    if not patches:
        sys.exit("no patch in " + series)
    print("%d patches to apply" % len(patches))
    # `git am` commits, so it needs an identity; a machine that has never
    # committed has none, and the run stopped at the first patch.
    identity = []
    if not subprocess.run(["git", "config", "user.email"], cwd=src,
                          capture_output=True).stdout.strip():
        identity = ["-c", "user.name=hdCycles bootstrap",
                    "-c", "user.email=bootstrap@hdcycles.local"]
    run(["git"] + identity + ["am"] + patches, cwd=src)


def prepare_usd_cxx20_compat(src, build, target, houdini=""):
    """Overlay the pinned USD constructor spelling rejected by GCC C++20.

    The dependency checkout stays untouched. This only removes a redundant
    template-id from a constructor, as fixed in upstream USD; layout and ABI
    are unchanged. A different defective header requires another review.
    """
    if not target.startswith("linux_"):
        return []
    relative = os.path.join("pxr", "imaging", "hd", "retainedDataSource.h")
    source = (os.path.join(houdini, "toolkit", "include", relative) if houdini else
              os.path.join(src, "lib", target, "usd", "include", relative))
    if not os.path.isfile(source):
        return []
    with open(source, "rb") as stream:
        original = stream.read()
    old = b"HdRetainedTypedSampledDataSource<bool>(const bool& value)"
    if old not in original:
        return []
    digest = hashlib.sha256(original).hexdigest()
    if digest != "db9dfe869b2c33e48b9b1f14e7b56ac378e4df85e00e22f1b5781478b83df19d":
        raise ValueError("Review USD's C++20 compatibility header before building: " + digest)
    corrected = original.replace(old, b"HdRetainedTypedSampledDataSource(const bool& value)")
    include = os.path.abspath(os.path.join(build, "usd-cxx20-compat", "include"))
    destination = os.path.join(include, relative)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "wb") as stream:
        stream.write(corrected)
    with open(os.path.join(build, "usd-cxx20-compat", "identity.json"), "w") as stream:
        json.dump({"source_sha256": digest,
                   "overlay_sha256": hashlib.sha256(corrected).hexdigest()}, stream, indent=2)
    return ["-DCMAKE_PROJECT_INCLUDE=" + os.path.join(HERE, "usd_cxx20_compat.cmake"),
            "-DCYCLES_USD_COMPAT_INCLUDE=" + include]


def configure(src, cfg, houdini, optix, arch, osl, split=True,
              build=None, install=None, extra=(), target=None):
    build = build or os.path.join(src, "build")
    target = target or library_platform()
    args = [
        "cmake",
        "-S", src,
        "-B", build,
        "-DCMAKE_INSTALL_PREFIX=" + (install or os.path.join(ROOT, cfg["install"])).replace("\\", "/"),
        "-DHOUDINI_ROOT=" + houdini.replace("\\", "/"),
        "-DWITH_CYCLES_HYDRA_RENDER_DELEGATE=ON",
        "-DWITH_CYCLES_OSL=" + ("ON" if osl else "OFF"),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DWITH_CYCLES_DEVICE_HIP=OFF",
        "-DWITH_CYCLES_DEVICE_ONEAPI=OFF",
        "-DWITH_CYCLES_CUDA_BUILD_SERIAL=ON",
    ]
    if target.startswith("macos_"):
        args += ["-DCMAKE_OSX_ARCHITECTURES=arm64", "-DWITH_CYCLES_DEVICE_METAL=ON",
                 "-DWITH_CYCLES_DEVICE_CUDA=OFF", "-DWITH_CYCLES_DEVICE_OPTIX=OFF",
                 "-DWITH_CYCLES_CUDA_BINARIES=OFF"]
    elif arch:
        args += ["-DWITH_CYCLES_CUDA_BINARIES=ON", "-DCYCLES_CUDA_BINARIES_ARCH=" + arch]
        # Cycles turns nvcc's split compile on only for one or two
        # architectures, to bound memory when several kernels build at once.
        # They build one after another here, and the flag cuts a kernel from
        # about 7.5 min to 1.5 min on a 128-thread machine. Always passed, empty
        # when off, so that a value cached by an earlier run cannot linger and
        # double up with the one Cycles adds itself.
        many = len([a for a in arch.split(";") if a]) > 2
        args += ["-DCUDA_NVCC_FLAGS=" + ("--split-compile=2" if split and many else "")]
    else:
        args += ["-DWITH_CYCLES_CUDA_BINARIES=OFF"]
    if optix and not target.startswith("macos_"):
        args += ["-DWITH_CYCLES_DEVICE_OPTIX=ON", "-DOPTIX_ROOT_DIR=" + optix.replace("\\", "/")]
    else:
        args += ["-DWITH_CYCLES_DEVICE_OPTIX=OFF"]
    compatibility = prepare_usd_cxx20_compat(src, build, target, houdini)
    if compatibility and any(arg.startswith("-DCMAKE_PROJECT_INCLUDE=") for arg in extra):
        raise ValueError("The USD compatibility overlay needs CMAKE_PROJECT_INCLUDE; combine custom hooks explicitly")
    if target.startswith("linux_") and not compatibility:
        # Disable an overlay cached by a previous configuration of this directory.
        compatibility = ["-DCYCLES_USD_COMPAT_INCLUDE="]
    run(args + compatibility + list(extra))
    return build


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", choices=sorted(VERSIONS), default="5.3")
    p.add_argument("--houdini", default=os.environ.get("HFS", ""),
                   help="Houdini install root (default: $HFS)")
    p.add_argument("--standalone", action="store_true",
                   help="build against open-source USD, without Houdini or SideFX libraries")
    p.add_argument("--source-dir", help="separate native Cycles source cache")
    p.add_argument("--build-dir", help="separate build directory (use one per SDK)")
    p.add_argument("--install-dir", help="installation prefix")
    p.add_argument("--prepare-only", action="store_true", help="fetch and patch source/dependencies only")
    p.add_argument("--cmake-arg", action="append", default=[], help="additional CMake argument (repeatable)")
    p.add_argument("--optix", default=os.environ.get("OPTIX_ROOT_DIR", ""),
                   help="OptiX SDK root; empty to build without OptiX")
    p.add_argument("--cuda-arch", default=CUDA_ARCH,
                   help="CUDA kernel architectures, ';'-separated (default: every NVIDIA "
                        "generation since Maxwell, plus a PTX fallback); "
                        "pass --cuda-arch= to build none")
    p.add_argument("--no-osl", action="store_true", help="build without OSL")
    p.add_argument("--no-split-compile", action="store_true",
                   help="do not pass nvcc --split-compile=2 when building more than two "
                        "CUDA architectures (slower, but lighter on memory)")
    p.add_argument("--jobs", type=int, default=2,
                   help="bounded parallel jobs (default: 2)")
    p.add_argument("--skip-build", action="store_true", help="stop after configuration")
    args = p.parse_args()

    if args.jobs < 1:
        p.error("--jobs must be positive")
    if args.standalone:
        args.houdini = ""
    if not args.houdini and not args.standalone and not args.prepare_only:
        sys.exit("pass --houdini, or set HFS. This version targets Houdini 22.0.368.")
    if args.houdini and not os.path.isdir(args.houdini):
        sys.exit("not found: " + args.houdini)
    for tool in ("git", "cmake"):
        if shutil.which(tool) is None:
            sys.exit(tool + " is missing from PATH")

    cfg = VERSIONS[args.version]
    src = os.path.abspath(args.source_dir or os.path.join(ROOT, "external", cfg["dir"]))
    identity = {"commit": cfg["commit"], "patch_sha256": patch_identity(args.version)}
    receipt = os.path.join(src, ".cycles-for-houdini.json")

    if os.path.exists(src):
        if not os.path.isfile(receipt):
            sys.exit("source has no completed patch receipt; use a new --source-dir: " + src)
        with open(receipt) as f:
            if json.load(f) != identity:
                sys.exit("source/patch identity changed; use a new --source-dir: " + src)
    else:
        print("== cloning Cycles at commit %s ==" % cfg["commit"][:8])
        clone(src, cfg)
        print("== applying series %s ==" % args.version)
        apply_patches(src, args.version)
        with open(receipt, "w") as f:
            json.dump(identity, f, indent=2)

    prepare_libraries(src, library_platform(), args.standalone and not args.no_osl)
    if args.prepare_only:
        return

    print("== configuration ==")
    build = configure(src, cfg, args.houdini, args.optix, args.cuda_arch, not args.no_osl,
                      not args.no_split_compile, args.build_dir, args.install_dir, args.cmake_arg)
    if args.skip_build:
        return

    print("== build and install ==")
    cmd = ["cmake", "--build", build, "--config", "Release", "--target", "install"]
    if args.jobs > 0:
        cmd += ["--parallel", str(args.jobs)]
    run(cmd)

    print("\nDone. Next: generate the VOP library, then assemble the folder for Houdini:\n"
          "    hython tools/build_cycles_vops.py\n"
          "    python tools/make_dist.py --version %s" % args.version)


if __name__ == "__main__":
    main()
