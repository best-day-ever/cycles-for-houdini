"""Render legacy Houdini texture and USD light-link fixtures with standalone Cycles.

Run with hython to generate a real Houdini RAT texture and read the EXRs.
The render command is the packaged, license-free cycles-usd-render.
"""

import argparse
from pathlib import Path
import subprocess
import tempfile

from bench_usdtex import mtlx_scene
import hou
import numpy as np
import OpenImageIO as oiio


LINK_SCENE = """#usda 1.0
(
    metersPerUnit = 1
)
def Camera "camera"
{
    token projection = "orthographic"
    float horizontalAperture = 40
    float verticalAperture = 40
    double3 xformOp:translate = (0, 0, 5)
    uniform token[] xformOpOrder = ["xformOp:translate"]
}

def Mesh "left"
{
    int[] faceVertexCounts = [4]
    int[] faceVertexIndices = [0, 1, 2, 3]
    point3f[] points = [(-2, -2, 0), (0, -2, 0), (0, 2, 0), (-2, 2, 0)]
    uniform token subdivisionScheme = "none"
}
def Mesh "right"
{
    int[] faceVertexCounts = [4]
    int[] faceVertexIndices = [0, 1, 2, 3]
    point3f[] points = [(0, -2, 0), (2, -2, 0), (2, 2, 0), (0, 2, 0)]
    uniform token subdivisionScheme = "none"
}
def DistantLight "key"
{
    float inputs:intensity = 2
    bool inputs:normalize = true
    bool collection:lightLink:includeRoot = false
    rel collection:lightLink:includes = </left>
}
"""


def render(runtime, work, name, scene, device):
    usd = work / (name + ".usda")
    exr = work / (name + ".exr")
    usd.write_text(scene, encoding="utf-8")
    subprocess.run(
        [str(runtime), str(usd), "--resolution", "64", "64", "--samples", "16",
         "--device", device, "--threads", "2", "--output", str(exr)],
        check=True, capture_output=True, text=True,
    )
    buf = oiio.ImageBuf(str(exr))
    image = buf.get_pixels(oiio.FLOAT)
    if buf.has_error or image.shape != (64, 64, 4) or not np.isfinite(image).all():
        raise RuntimeError("Invalid EXR from " + name)
    return image[:, :, :3]


def qualify(runtime, work, device):
    texture = work / "source.exr"
    rgb = np.tile(np.array([0.2, 0.4, 0.6], dtype=np.float32), (8, 8, 1))
    buf = oiio.ImageBuf(oiio.ImageSpec(8, 8, 3, oiio.FLOAT))
    buf.set_pixels(oiio.ROI(0, 8, 0, 8, 0, 1, 0, 3), rgb)
    if not buf.write(str(texture)):
        raise RuntimeError(buf.geterror())
    rat = work / "source.exr.rat"
    subprocess.run([hou.text.expandString("$HFS/bin/iconvert"), str(texture), str(rat)], check=True)
    source = render(runtime, work, "original", mtlx_scene(texture.name, ""), device)
    converted = render(runtime, work, "rat", mtlx_scene(rat.name, ""), device)
    source_interior = source[24:40, 24:40]
    difference = np.abs(converted[24:40, 24:40] - source_interior).max()
    print(f"RAT source pixels: maximum difference {difference:.6f}", flush=True)
    rat_ok = source_interior.mean() >= 0.1 and difference <= 0.01

    image = render(runtime, work, "light-link", LINK_SCENE, device)
    left = image[24:40, 8:24].mean()
    right = image[24:40, 40:56].mean()
    print(f"Linked light: left {left:.6f}, right {right:.6f}", flush=True)
    return rat_ok and left >= 0.1 and right <= 0.01


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path, help="packaged cycles-usd-render executable")
    parser.add_argument("--device", default="CPU", choices=("CPU", "OPTIX", "CUDA", "METAL"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cycles-legacy-usd-") as work:
        return 0 if qualify(args.runtime.resolve(), Path(work), args.device) else 1


if __name__ == "__main__":
    raise SystemExit(main())
