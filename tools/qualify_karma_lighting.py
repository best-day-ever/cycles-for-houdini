"""Check Karma light contribution and OIDN beauty settings on exported USD."""

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np
import OpenImageIO as oiio

from qualify_karma_shadow import SCENE
from qualify_karma_transmission import sample_pixels, scene_text
from qualify_legacy_usd import LINK_SCENE, render


def test_refraction(runtime, work, device):
    scene = scene_text(0.25, 0.0)
    start = scene.index('    def DomeLight')
    stop = scene.index('    def Mesh "box"', start)
    passed = True
    for light_type, parameters in (
        ("DomeLight", ""),
        ("RectLight", """
        float inputs:width = 12
        float inputs:height = 12
        double3 xformOp:translate = (0, 0, -2)
        float3 xformOp:rotateXYZ = (0, 180, 0)
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]
        """),
    ):
        template = scene[:start] + f'''    def {light_type} "key"
    {{
        float inputs:intensity = 1
        {parameters}
        %s
    }}
''' + scene[stop:]
        on = render(runtime, work, light_type + "-refraction-on", template % "", device)
        off = render(runtime, work, light_type + "-refraction-off", template %
                     'string inputs:karma:light:contribs = "-refract"', device)
        on_glass, off_glass = (sample_pixels(image).mean() for image in (on, off))
        on_background, off_background = (image[:10, :10].mean() for image in (on, off))
        print(f"{light_type} glass {on_glass:.6f} -> {off_glass:.6f}, "
              f"background {on_background:.6f} -> {off_background:.6f}", flush=True)
        passed &= (on_glass > 0.1 and off_glass < 0.01 and
                   abs(off_background - on_background) < 0.01)
        if light_type == "DomeLight":
            passed &= on_background > 0.9
    return passed


def test_dome_intensity(runtime, work, device):
    hdr = oiio.ImageOutput.create(str(work / "dome.exr"))
    if not hdr or not hdr.open(str(work / "dome.exr"), oiio.ImageSpec(16, 8, 3, oiio.FLOAT)):
        raise RuntimeError("Could not create dome EXR")
    if not hdr.write_image(np.ones((8, 16, 3), dtype=np.float32)) or not hdr.close():
        raise RuntimeError("Could not write dome EXR")

    scene = LINK_SCENE[:LINK_SCENE.index('def Mesh "right"')] + """
def DomeLight "dome"
{
    float inputs:intensity = %s
    asset inputs:texture:file = @./dome.exr@
}
"""
    intensities = (0.25, 1.0, 4.0)
    receiver, background = [], []
    for intensity in intensities:
        image = render(runtime, work, f"dome-{intensity}", scene % intensity, device)
        receiver.append(float(image[16:48, 6:24].mean()))
        background.append(float(image[16:48, 40:58].mean()))
    print(f"Dome intensity {intensities}, receiver {np.round(receiver, 6)}, "
          f"background {np.round(background, 6)}", flush=True)
    return (receiver[1] > 0.1 and
            np.allclose(receiver, np.array(intensities) * receiver[1], rtol=0.02) and
            np.allclose(background, intensities, atol=0.001))


def test_denoise(runtime, work, device):
    scene = (SCENE % {"opacity": "", "shader": ""}).replace(
        "metersPerUnit = 1", 'metersPerUnit = 1\n    renderSettingsPrimPath = "/Settings"'
    ).replace('def DistantLight "key"', 'def SphereLight "key"').replace(
        "    float inputs:intensity = 2", """    float inputs:intensity = 160
    float inputs:radius = 0.8
    double3 xformOp:translate = (-1, 0, 2)
"""
    ).replace('["xformOp:rotateXYZ"]', '["xformOp:translate", "xformOp:rotateXYZ"]')
    variants = (
        ("raw", "", ""),
        ("explicit", "", "bool cycles:integrator:use_denoise = true"),
        ("inherited", "C", ""),
        ("auxiliary", "C_Fill", ""),
        ("overridden", "C", "bool cycles:integrator:use_denoise = false"),
    )
    results = {}
    for name, aov, override in variants:
        spec = json.dumps([["denoise", {
            "engine": "oidn", "use_albedo_input": True, "use_n_input": True,
            "forcecpu": False, "aovs": [aov],
        }]])
        authored = f"string karma:global:imagefilter = {json.dumps(spec)}" if aov else ""
        settings = f'''
def RenderSettings "Settings"
{{
    rel camera = </camera>
    {authored}
    {override}
}}
'''
        results[name] = render(runtime, work, name, scene + settings, device)
    def error(a, b):
        return float(np.abs(results[a] - results[b]).mean())

    denoise = error("raw", "explicit")
    inherited = error("explicit", "inherited")
    auxiliary = error("raw", "auxiliary")
    overridden = error("raw", "overridden")
    print(f"RGB deltas raw to denoised {denoise:.6f}, explicit to inherited {inherited:.6f}, "
          f"raw to auxiliary {auxiliary:.6f}, raw to overridden {overridden:.6f}", flush=True)
    return denoise > 0.0005 and max(inherited, auxiliary, overridden) < 0.0001


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--device", default="CPU", choices=("CPU", "OPTIX", "CUDA", "METAL"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cycles-karma-lighting-") as directory:
        runtime, work = args.runtime.resolve(), Path(directory)
        refraction = test_refraction(runtime, work, args.device)
        dome = test_dome_intensity(runtime, work, args.device)
        denoise = test_denoise(runtime, work, args.device)
    return 0 if refraction and dome and denoise else 1


if __name__ == "__main__":
    raise SystemExit(main())
