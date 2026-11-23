"""Compare Standard Surface transmission across closed slabs.

Run with the exact Houdini SDK's hython. The optional Husk reference uses
Karma XPU. Standalone Cycles gets the same USD and uses no Houdini libraries.
"""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
import OpenImageIO as oiio

from bench_volume import MTLX_VOLUME, OUTWARD, SCENE, SIGMA
from qualify_legacy_usd import render


TINT = (0.25, 0.5, 0.8)


def scene_text(thickness, depth, units=1.0, connected=False, thin=False, volume=""):
    depth_input = f"float inputs:transmission_depth = {depth}"
    extra = ""
    if connected:
        depth_input = (
            "float inputs:transmission_depth.connect = "
            "</world/Materials/M/depth.outputs:out>"
        )
        extra = f"""def Shader "depth"
            {{
                uniform token info:id = "ND_constant_float"
                float inputs:value = {depth}
                float outputs:out
            }}"""
    thin_input = "bool inputs:thin_walled = true" if thin else ""
    material = f"""
            token outputs:mtlx:surface.connect = </world/Materials/M/glass.outputs:out>
            def Shader "glass"
            {{
                uniform token info:id = "ND_standard_surface_surfaceshader"
                float inputs:base = 0
                float inputs:specular = 1
                float inputs:specular_IOR = 1
                float inputs:specular_roughness = 0
                float inputs:transmission = 1
                color3f inputs:transmission_color = {TINT}
                {depth_input}
                {thin_input}
                token outputs:out
            }}
            {extra}
            {volume}
"""
    scene = SCENE % {"orientation": "rightHanded", "indices": OUTWARD, "volume": material}
    scene = scene.replace("metersPerUnit = 1", f"metersPerUnit = {units}")
    points = [
        (x, y, z * thickness / 2)
        for x, y, z in (
            (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1),
            (-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1),
        )
    ]
    pattern = r"point3f\[\] points = \[\(-1,-1,-1\).*?\]"
    scene, count = re.subn(pattern, f"point3f[] points = {points}", scene)
    if count != 1:
        raise RuntimeError("Could not set slab thickness")
    return scene


def sample_pixels(image):
    if image is None or image.shape[:2] != (64, 64) or not np.isfinite(image).all():
        raise RuntimeError("Invalid render")
    return image[29:35, 29:35, :3].mean(axis=(0, 1))


def karma(husk, work, name):
    source = work / (name + ".usda")
    output = work / (name + "-karma.exr")
    result = subprocess.run(
        [str(husk), "--engine", "xpu", "--threads", "3", "--res", "64", "64",
         "--timelimit", "45", "--timelimit-nosave-partial", "--output", str(output),
         str(source)],
        capture_output=True, text=True, timeout=55,
    )
    if not output.is_file():
        raise RuntimeError(f"Karma did not write {name}\n{result.stdout}\n{result.stderr}")
    image = oiio.ImageBuf(str(output)).get_pixels(oiio.FLOAT)
    return sample_pixels(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--device", default="CPU", choices=("CPU", "OPTIX", "CUDA", "METAL"))
    parser.add_argument("--husk", type=Path)
    args = parser.parse_args()
    cases = (
        ("short", 0.25, 0.5, 1.0, False),
        ("long", 0.75, 0.5, 1.0, False),
        ("connected", 0.75, 0.5, 1.0, True),
        ("centimetres", 0.75, 0.5, 0.01, True),
        ("zero-depth", 0.75, 0.0, 1.0, False),
    )
    passed = True
    with tempfile.TemporaryDirectory(prefix="cycles-karma-transmission-") as directory:
        work = Path(directory)
        for name, thickness, depth, units, connected in cases:
            scene = scene_text(thickness, depth, units, connected)
            cycles = sample_pixels(render(args.runtime.resolve(), work, name, scene, args.device))
            expected = np.power(TINT, thickness / depth if depth else 2.0)
            error = np.abs(cycles - expected).max()
            passed &= error < 0.025
            print(f"{name}: Cycles {cycles.round(5)}, expected {expected.round(5)}",
                  flush=True)
            if args.husk:
                reference = karma(args.husk, work, name)
                print(f"  Karma {reference.round(5)}, error {np.abs(cycles - reference).max():.6f}",
                      flush=True)
                passed &= np.abs(cycles - reference).max() < 0.035
        tint = np.square(TINT)
        for name, scene, expected in (
            ("thin-walled", scene_text(0.75, 0.5, thin=True), tint),
            ("authored-volume", scene_text(0.75, 0.5, volume=MTLX_VOLUME),
             tint * np.exp(-0.75 * np.array(SIGMA))),
        ):
            cycles = sample_pixels(render(args.runtime.resolve(), work, name, scene, args.device))
            error = np.abs(cycles - expected).max()
            passed &= error < 0.025
            print(f"{name}: Cycles {cycles.round(5)}, expected {expected.round(5)}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
