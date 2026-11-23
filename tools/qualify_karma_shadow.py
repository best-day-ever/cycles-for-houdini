"""Check Karma's shadow-ray opacity switch through standalone Cycles."""

import argparse
from pathlib import Path
import tempfile

from qualify_legacy_usd import render


SCENE = """#usda 1.0
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
def Mesh "floor"
{
    int[] faceVertexCounts = [4]
    int[] faceVertexIndices = [0, 1, 2, 3]
    point3f[] points = [(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)]
    uniform token subdivisionScheme = "none"
}
def Mesh "blocker" (prepend apiSchemas = ["MaterialBindingAPI"])
{
    rel material:binding = </Material>
    int[] faceVertexCounts = [4]
    int[] faceVertexIndices = [0, 1, 2, 3]
    point3f[] points = [(-0.3, -0.3, 1), (0.3, -0.3, 1), (0.3, 0.3, 1), (-0.3, 0.3, 1)]
    uniform token subdivisionScheme = "none"
}
def DistantLight "key"
{
    float inputs:intensity = 2
    bool inputs:normalize = true
    float3 xformOp:rotateXYZ = (0, -45, 0)
    uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]
}
def Material "Material"
{
    token outputs:mtlx:surface.connect = </Material/surface.outputs:out>
    def Shader "surface"
    {
        uniform token info:id = "ND_standard_surface_surfaceshader"
        color3f inputs:base_color = (0.8, 0.05, 0.05)
        float inputs:specular = 0
        %(opacity)s
        token outputs:out
    }
    %(shader)s
}
"""

RAY_IMPORT = """
    def Shader "shadow"
    {
        uniform token info:id = "kma_rayimport_bool"
        string inputs:name = "ray:isshadowray"
        bool outputs:out
    }
    def Shader "invert"
    {
        uniform token info:id = "ND_invert_float"
        float inputs:in.connect = </Material/shadow.outputs:out>
        float outputs:out
    }
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--device", default="CPU", choices=("CPU", "OPTIX", "CUDA", "METAL"))
    args = parser.parse_args()
    opaque = SCENE % {"opacity": "", "shader": ""}
    cutout = SCENE % {
        "opacity": "color3f inputs:opacity.connect = </Material/invert.outputs:out>",
        "shader": RAY_IMPORT,
    }
    with tempfile.TemporaryDirectory(prefix="cycles-karma-shadow-") as directory:
        work = Path(directory)
        solid = render(args.runtime.resolve(), work, "opaque", opaque, args.device)
        switched = render(args.runtime.resolve(), work, "ray-switch", cutout, args.device)
    shadow = solid[29:35, 45:51].mean()
    lit = solid[29:35, 13:19].mean()
    opened = switched[29:35, 45:51].mean()
    colors = switched[30:34, 30:34].mean(axis=(0, 1))
    print(f"Shadow {shadow:.6f}, open {opened:.6f}, lit {lit:.6f}, blocker {colors.round(5)}")
    passed = lit > 0.1 and shadow < lit * 0.2 and opened > lit * 0.9 and colors[0] > 4 * colors[1]
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
