"""Render an unchanged Karma XPU scene through Cycles on the exact Houdini SDK.

Run with hython from a licensed Houdini session and the candidate package in
its search path. The optional standalone wrapper must point to the matching
candidate runtime.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

import hou
import numpy as np
import OpenImageIO as oiio
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender, UsdShade


SETTINGS = "/Render/rendersettings"
CAMERA = "/World/cameras/chosen"
RESOLUTION = (144, 96)
KARMA_SAMPLES = 11
EXPORTED_SAMPLES = 64
NATIVE_SAMPLES = 5
CLI_SAMPLES = 3


def author(stage):
    camera = UsdGeom.Camera.Define(stage, CAMERA)
    camera.AddTranslateOp().Set(Gf.Vec3d(0, 0, 8))
    camera.CreateFocalLengthAttr(32)
    camera.CreateHorizontalApertureAttr(36)
    camera.CreateVerticalApertureAttr(24)
    wrong_camera = UsdGeom.Camera.Define(stage, "/World/cameras/aaa_wrong")
    wrong_camera.AddTranslateOp().Set(Gf.Vec3d(20, 0, 8))

    material = UsdShade.Material.Define(stage, "/World/looks/red")
    shader = UsdShade.Shader.Define(stage, "/World/looks/red/surface")
    shader.CreateIdAttr("ND_standard_surface_surfaceshader")
    shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.65, .14, .04))
    shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(.2)
    shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(.35)
    shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput("kma").ConnectToSource(shader.ConnectableAPI(), "out")

    # A Karma Material Builder can author a second, simplified viewport
    # material. Give it a visibly different color to detect a fallback.
    preview = UsdShade.Shader.Define(stage, "/World/looks/red/preview")
    preview.CreateIdAttr("UsdPreviewSurface")
    preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.04, .65, .04))
    preview.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), "surface")

    sphere = UsdGeom.Sphere.Define(stage, "/World/ball")
    sphere.CreateRadiusAttr(1.3)
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    light = UsdLux.RectLight.Define(stage, "/World/key")
    light.AddTranslateOp().Set(Gf.Vec3d(-1, 1, 5))
    light.CreateIntensityAttr(6)


def save_scene(output):
    parent = hou.node("/stage")
    look = parent.createNode("pythonscript", "shared_materialx")
    script = Path(__file__).resolve()
    look.parm("python").set(
        "import hou, runpy\n"
        + f"runpy.run_path({str(script)!r}, run_name='fixture')['author'](hou.pwd().editableStage())\n"
    )

    render = parent.createNode("karmarenderproperties", "karma_xpu")
    render.setInput(0, look)
    render.parm("engine").set("xpu")
    render.parm("pathtracedsamples").set(KARMA_SAMPLES)
    render.parm("resolutionx").set(RESOLUTION[0])
    render.parm("camera").set(CAMERA)
    render.parm("picture").set(str(output / "authored.exr"))

    hip = output / "karma-switch.hiplc"
    usd = output / "karma-switch.usda"
    node_path = render.path()
    hou.hipFile.save(str(hip))
    hou.hipFile.load(str(hip), suppress_save_prompt=True, ignore_load_warnings=True)
    render = hou.node(node_path)
    assert hou.licenseCategory() == hou.licenseCategoryType.Indie
    stage = render.stage()
    assert stage and not render.errors(), render.errors()
    settings = UsdRender.Settings.Get(stage, SETTINGS)
    assert stage.GetMetadata("renderSettingsPrimPath") == SETTINGS
    assert tuple(settings.GetResolutionAttr().Get()) == RESOLUTION
    assert [str(path) for path in settings.GetCameraRel().GetTargets()] == [CAMERA]
    assert settings.GetPrim().GetAttribute("karma:global:pathtracedsamples").Get() == KARMA_SAMPLES
    assert not settings.GetPrim().GetAttribute("cycles:samples")
    assert stage.Export(str(usd))
    return hip, usd


def render_image(command, usd, output, name):
    image_path = output / (name + ".exr")
    started = time.monotonic()
    with (output / (name + ".log")).open("w") as log:
        subprocess.run([*command, "--output", str(image_path), str(usd)], cwd=output,
                       stdout=log, stderr=subprocess.STDOUT, timeout=150, check=True)
    elapsed = time.monotonic() - started
    image = oiio.ImageBuf(str(image_path))
    assert image.read(), image.geterror()
    assert (image.spec().width, image.spec().height, image.spec().nchannels) == (*RESOLUTION, 4)
    rgb = image.get_pixels(oiio.FLOAT)[:, :, :3]
    assert np.isfinite(rgb).all()
    center = rgb[40:56, 64:80].mean(axis=(0, 1))
    assert center[0] > .02 and center[0] > center[1] > center[2], (name, center)
    stats = {
        "wall_seconds": elapsed,
        "center_mean_rgb": [float(channel) for channel in center],
        "sample_limit": image.spec().get_int_attribute("cyclesSampleLimit", -1),
    }
    return stats


def assert_standalone_samples(output, name, expected):
    log = (output / (name + ".log")).read_text()
    counts = re.findall(r"\bSample\s+\d+/(\d+)", log)
    assert counts and {int(count) for count in counts} == {expected}, (name, counts)
    return int(counts[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--delegate", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--standalone", type=Path, help="Executable standalone render wrapper")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    assert hou.applicationVersionString() == "22.0.466"
    assert hou.licenseCategory() == hou.licenseCategoryType.Indie
    hip, source = save_scene(output)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (hip, source)]

    override = output / "cycles-override.usda"
    cpu_karma = output / "karma-cpu-settings.usda"
    exported_karma = output / "karma-export-without-engine.usda"
    override_stage = Usd.Stage.Open(str(source))
    prim = override_stage.GetPrimAtPath(SETTINGS)
    prim.CreateAttribute("cycles:samples", Sdf.ValueTypeNames.Int).Set(NATIVE_SAMPLES)
    assert override_stage.Export(str(override))
    prim.RemoveProperty("cycles:samples")
    prim.GetAttribute("karma:global:engine").Set("cpu")
    assert override_stage.Export(str(cpu_karma))
    prim.RemoveProperty("karma:global:engine")
    prim.GetAttribute("karma:global:pathtracedsamples").Set(EXPORTED_SAMPLES)
    assert not prim.GetAttribute("karma:global:engine").HasAuthoredValueOpinion()
    assert override_stage.Export(str(exported_karma))

    suffix = ".exe" if os.name == "nt" else ""
    husk = str(Path(hou.getenv("HFS")) / "bin" / ("husk" + suffix))
    cycles_delegate = "HdCyclesPluginGPU" if args.delegate == "gpu" else "HdCyclesPlugin"
    cycles = [husk, "--renderer", cycles_delegate, "--threads", "4", "-V", "5"]
    record = {"houdini": hou.applicationVersionString(), "license": str(hou.licenseCategory())}
    record["cycles"] = render_image(cycles, source, output, "cycles")
    assert record["cycles"]["sample_limit"] == KARMA_SAMPLES, record["cycles"]
    record["native_override"] = render_image(cycles, override, output, "cycles-override")
    assert record["native_override"]["sample_limit"] == NATIVE_SAMPLES
    record["cpu_settings"] = render_image(cycles, cpu_karma, output, "cycles-cpu-settings")
    assert record["cpu_settings"]["sample_limit"] == 1024
    record["unmarked_export"] = render_image(cycles, exported_karma, output, "cycles-unmarked-export")
    assert record["unmarked_export"]["sample_limit"] == EXPORTED_SAMPLES

    if args.standalone:
        command = [str(args.standalone.resolve()), "--device", args.delegate.upper(),
                   "--threads", "4"]
        for name, stage, extra, count in (
            ("standalone", source, [], KARMA_SAMPLES),
            ("standalone-unmarked-export", exported_karma, [], EXPORTED_SAMPLES),
            ("standalone-native-override", override, [], NATIVE_SAMPLES),
            ("standalone-cli-override", override, ["--samples", str(CLI_SAMPLES)], CLI_SAMPLES),
        ):
            record[name] = render_image([*command, *extra], stage, output, name)
            record[name]["sample_limit"] = assert_standalone_samples(output, name, count)

    after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (hip, source)]
    assert before == after
    record["source_sha256"] = before
    print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    main()
