"""Assembles the folder that is handed to users, from an install tree.

    python tools/make_dist.py --version 5.3 [--zip]

The result, in dist/CyclesForHoudini-<version>/:

    cycles.json              the Houdini package
    CyclesForHoudini/        the plugin (houdini/, licenses/, LICENSE, NOTICE)

Both are dropped together into a Houdini `packages` folder (for example
Documents/houdini22.0/packages). The package finds the plugin through
$HOUDINI_PACKAGE_PATH, so nothing in either holds a path of this machine.

The package file is taken from the template in the Cycles clone rather than
from the install tree, which may have been built before the template went
relative. The tool then scans what it copies and refuses to finish if a text
file still names a drive letter.
"""

import argparse
import gzip
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from bootstrap import library_platform, verify_payload_files

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

VERSIONS = {
    "5.3": {"dir": "cycles-53", "install": "install-53"},
}

PLUGIN_DIR = "CyclesForHoudini"
SKIP_DIRS = {"backup", "__pycache__", "packages"}
TEXT_EXT = {".json", ".py", ".ds", ".xml", ".shelf", ".txt", ".cmake"}
ABSOLUTE = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/][A-Za-z0-9_$ .-]")
ASSET_SUFFIXES = {".hda", ".hdalc", ".hdanc", ".otl", ".otllc", ".otlnc"}


def copy_tree(src, dst):
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(base, src)
        target = os.path.normpath(os.path.join(dst, rel))
        os.makedirs(target, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".bak")) or ".old-" in name:
                continue
            shutil.copy2(os.path.join(base, name), os.path.join(target, name))


def scan(dist):
    bad = []
    for base, _, files in os.walk(dist):
        for name in files:
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                if ABSOLUTE.search(f.read()):
                    bad.append(path)
    return bad


def verify_houdini_assets(otls, hotl):
    """Inspect embedded node networks as well as the library file name."""
    libraries = sorted(path for path in Path(otls).rglob("*")
                       if path.is_file() and path.suffix.lower() in ASSET_SUFFIXES)
    if not libraries:
        raise ValueError(f"No Houdini asset libraries found in {otls}")
    with tempfile.TemporaryDirectory(prefix="cycles-hda-check-") as temporary:
        for index, library in enumerate(libraries):
            if library.suffix.lower() in {".hdanc", ".otlnc"}:
                raise ValueError(f"Non-Commercial Houdini asset library: {library}")
            expanded = Path(temporary) / str(index)
            subprocess.run([hotl, "-x", str(expanded), str(library)], check=True)
            for contents in expanded.rglob("Contents.gz"):
                # Houdini prefixes the embedded network with a four-byte size.
                with gzip.open(contents, "rb") as stream:
                    signature = stream.read(9)[4:]
                if signature == b"HouNC":
                    raise ValueError(f"Non-Commercial embedded Houdini network: {library} ({contents.parent.name})")


def relocate_linux_delegate(plugin, houdini_runtime=""):
    """Fix file-installed delegates, for which CMake skips install RPATH edits."""
    if not sys.platform.startswith("linux"):
        return
    delegate = os.path.join(plugin, "houdini", "dso", "usd", "hdCycles.so")
    dynamic = subprocess.check_output(["readelf", "-d", delegate], text=True)
    match = re.search(r"\((?:RPATH|RUNPATH)\).*?\[([^\]]+)\]", dynamic)
    if not match:
        raise ValueError("Missing native delegate library path: " + delegate)
    relative = "$ORIGIN/../../lib"
    if houdini_runtime:
        if not os.path.isabs(houdini_runtime):
            raise ValueError("The Houdini runtime prefix must be absolute")
        relative += ":" + houdini_runtime + "/dsolib:" + houdini_runtime + "/python/lib"
    if match.group(1) == relative:
        return
    with tempfile.TemporaryDirectory(prefix="cycles-rpath-") as temporary:
        script = os.path.join(temporary, "relocate.cmake")
        with open(script, "w") as stream:
            stream.write("file(RPATH_CHANGE FILE [=[" + delegate + "]=] OLD_RPATH [=[" +
                         match.group(1) + "]=] NEW_RPATH [=[" + relative + "]=])\n")
        subprocess.run(["cmake", "-P", script], check=True)
    relocated = subprocess.check_output(["readelf", "-d", delegate], text=True)
    if not re.search(r"\((?:RPATH|RUNPATH)\).*?\[" + re.escape(relative) + r"\]", relocated):
        raise ValueError("Delegate relocation failed: " + delegate)


def relocate_macos_delegate(plugin, houdini_runtime=""):
    if sys.platform != "darwin":
        return
    delegate = os.path.join(plugin, "houdini", "dso", "usd", "hdCycles.dylib")
    dynamic = subprocess.check_output(["otool", "-l", delegate], text=True)
    paths = re.findall(r"\bcmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", dynamic)
    for path in paths:
        subprocess.run(["install_name_tool", "-delete_rpath", path, delegate], check=True)
    paths = ["@loader_path/../../lib"]
    if houdini_runtime:
        if not os.path.isabs(houdini_runtime):
            raise ValueError("The Houdini runtime prefix must be absolute")
        paths += [houdini_runtime + "/Frameworks/Houdini.framework/Libraries",
                  houdini_runtime + "/Frameworks/Python.framework/Versions/Current/lib"]
    for path in paths:
        subprocess.run(["install_name_tool", "-add_rpath", path, delegate], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", delegate], check=True)
    subprocess.run(["codesign", "--verify", "--strict", delegate], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", choices=sorted(VERSIONS), default="5.3")
    p.add_argument("--zip", action="store_true", help="also write dist/<name>.zip")
    p.add_argument("--install-dir", help="native SDK installation prefix")
    p.add_argument("--name", help="platform/SDK-specific archive name")
    p.add_argument("--hotl", default=shutil.which("hotl"),
                   help="Exact Houdini SDK's hotl utility (default: hotl on PATH)")
    p.add_argument("--houdini-runtime", default="",
                   help="Target's exact installed Houdini prefix (for SDK library lookup)")
    args = p.parse_args()
    if not args.hotl:
        p.error("Use the exact Houdini SDK's hotl on PATH or pass --hotl")
    if sys.platform.startswith("linux") and not args.houdini_runtime:
        p.error("Linux clients need --houdini-runtime with the target's exact SDK prefix")

    cfg = VERSIONS[args.version]
    install = os.path.abspath(args.install_dir or os.path.join(ROOT, cfg["install"]))
    houdini = os.path.join(install, "houdini")
    template = os.path.join(ROOT, "external", cfg["dir"], "src", "hydra",
                            "resources", "cycles.json.in")
    for path in (houdini, template):
        if not os.path.exists(path):
            sys.exit("not found: " + path)

    with open(template, "rb") as f:
        package = f.read()
    if b"@" in package or ABSOLUTE.search(package):
        sys.exit("the package template still holds a build path: " + template)

    name = args.name or "CyclesForHoudini-" + args.version
    if name != os.path.basename(name) or name in (".", ".."):
        sys.exit("--name must be a single directory name")
    dist = os.path.join(ROOT, "dist", name)
    if os.path.exists(dist):
        shutil.rmtree(dist)
    plugin = os.path.join(dist, PLUGIN_DIR)

    copy_tree(houdini, os.path.join(plugin, "houdini"))
    verify_houdini_assets(os.path.join(plugin, "houdini", "otls"), args.hotl)
    relocate_linux_delegate(plugin, args.houdini_runtime)
    relocate_macos_delegate(plugin, args.houdini_runtime)
    licence = os.path.join(install, "license")
    if os.path.isdir(licence):
        copy_tree(licence, os.path.join(plugin, "licenses"))
    dependencies = os.path.join(ROOT, "external", cfg["dir"], "lib", library_platform(), "deps.md")
    if os.path.isfile(dependencies):
        os.makedirs(os.path.join(plugin, "licenses"), exist_ok=True)
        shutil.copy2(dependencies, os.path.join(plugin, "licenses", "Blender-dependencies.md"))
    for f in ("LICENSE", "NOTICE"):
        shutil.copy2(os.path.join(ROOT, f), os.path.join(plugin, f))
    with open(os.path.join(dist, "cycles.json"), "wb") as f:
        f.write(package)

    verify_payload_files(dist)
    bad = scan(dist)
    if bad:
        print("text files that still name a drive letter:")
        for b in bad:
            print("  " + b)
        sys.exit(1)

    print("Done: " + dist)
    if args.zip:
        archive = shutil.make_archive(dist, "zip", root_dir=dist)
        print("Zip:  " + archive)


if __name__ == "__main__":
    main()
