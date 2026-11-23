"""Native Indie GUI acceptance for the packaged Solaris LOPs.

Place this module on the test preferences' Python path and call run() from
uiready.py after the UI starts. Set CYCLES_SOLARIS_QA_DIR to a scratch directory,
CYCLES_SOLARIS_QA_OTLS to the relocated package's otls, and CYCLES_SOLARIS_QA_PHASE
to create or reopen. Run each phase in a fresh Houdini Indie process.
"""

import json
import os
from pathlib import Path
import traceback

import hou
from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdRender, UsdShade


SETTINGS = "/Render/CyclesRenderSettings"
PRODUCTS = "/Render/Products"
VARS = f"{PRODUCTS}/Vars"
CAMERA_A = "/World/CameraA"
CAMERA_B = "/World/CameraB"


def create_stage(node):
    stage = node.editableStage()
    for path, x in ((CAMERA_A, 0), (CAMERA_B, 2)):
        camera = UsdGeom.Camera.Define(stage, path)
        camera.AddTranslateOp().Set(Gf.Vec3d(x, 0, 6))
        camera.CreateFocalLengthAttr(35)
        camera.CreateHorizontalApertureAttr(36)
        camera.CreateVerticalApertureAttr(24)
    light = UsdLux.DistantLight.Define(stage, "/World/Sun")
    light.CreateIntensityAttr(3)
    light.CreateNormalizeAttr(True)
    for name, x, color in (("A", -1, (0.8, 0.1, 0.1)), ("B", 1, (0.1, 0.7, 0.1))):
        sphere = UsdGeom.Sphere.Define(stage, f"/World/Asset{name}/Sphere")
        sphere.CreateRadiusAttr(0.8)
        sphere.AddTranslateOp().Set(Gf.Vec3d(x, 0, 0))
        material = UsdShade.Material.Define(stage, f"/World/Material{name}")
        shader = UsdShade.Shader.Define(stage, f"/World/Material{name}/Diffuse")
        shader.CreateIdAttr("cycles_diffuse_bsdf")
        shader.CreateInput("color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        material.CreateSurfaceOutput("cycles").ConnectToSource(shader.ConnectableAPI(), "closure")
        UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)

    settings = UsdRender.Settings.Define(stage, SETTINGS)
    settings.CreateResolutionAttr(Gf.Vec2i(96, 64))
    settings.CreateCameraRel().SetTargets([CAMERA_A])
    settings.GetPrim().CreateAttribute("cycles:samples", Sdf.ValueTypeNames.Int).Set(4)
    for name, source, kind, channel in (("Beauty", "color", "color4f", "C"),
                                        ("Depth", "depth", "float", "Pz")):
        var = UsdRender.Var.Define(stage, f"{VARS}/{name}")
        var.CreateSourceNameAttr(source)
        var.CreateSourceTypeAttr("raw")
        var.CreateDataTypeAttr(kind)
        var.GetPrim().AddAppliedSchema("HuskRenderVarAPI")
        var.GetPrim().CreateAttribute("driver:parameters:aov:husk:name", Sdf.ValueTypeNames.String).Set(channel)


def make_network(directory):
    network = hou.node("/stage")
    scene = network.createNode("pythonscript", "qa_scene")
    scene.parm("python").set("from verify_solaris_indie import create_stage\ncreate_stage(hou.pwd())")
    products = network.createNode("hdcycles::cycles_render_products::1.0", "qa_products")
    products.setFirstInput(scene)
    products.parm("products").set(3)
    products.parm("camera").set(CAMERA_A)
    products.parm("colorspace").set("ACES2065-1")
    products.parm("orderedvars").set(f"{VARS}/Beauty {VARS}/Depth")
    for index, name in ((1, "Beauty"), (2, "Alternate")):
        products.parm(f"name{index}").set(name)
        products.parm(f"picture{index}").set(str(directory / f"{name}.%04d.exr"))
    products.parm("camera2").set(CAMERA_B)
    products.parm("csmode2").set("set")
    products.parm("colorspace2").set("Raw")
    products.parm("enable3").set(False)

    mattes = network.createNode("hdcycles::cycles_cryptomatte::1.0", "qa_add_mattes")
    mattes.setFirstInput(products)
    mattes.parm("products").set(f"{PRODUCTS}/Beau*")
    for kind, ranks in (("object", 3), ("material", 2), ("asset", 2)):
        mattes.parm(f"{kind}ranks").set(ranks)

    separate = network.createNode("hdcycles::cycles_cryptomatte::1.0", "qa_matte_exr")
    separate.setFirstInput(mattes)
    separate.parm("mode").set("separate")
    separate.parm("objectranks").set(2)
    separate.parm("picture").set(str(directory / "Mattes.%04d.exr"))
    separate.parm("comment").set("Cycles Solaris Indie QA")
    separate.setDisplayFlag(True)
    network.layoutChildren()


def assert_indie():
    assert hou.isUIAvailable(), "This test requires the desktop GUI"
    assert hou.licenseCategory() == hou.licenseCategoryType.Indie, hou.licenseCategory()


def verify_network(otls):
    for name, library in (("qa_products", "hdcycles_render_products.hda"),
                          ("qa_add_mattes", "hdcycles_cryptomatte.hda"),
                          ("qa_matte_exr", "hdcycles_cryptomatte.hda")):
        node = hou.node(f"/stage/{name}")
        assert Path(node.type().definition().libraryFilePath()).resolve() == otls / library
        try:
            node.cook(force=True)
        except hou.OperationFailed as exc:
            raise AssertionError(f"{name}: {node.errors()}") from exc
        assert not node.errors(), node.errors()
        assert_indie()

    stage = hou.node("/stage/qa_matte_exr").stage()
    settings = UsdRender.Settings.Get(stage, SETTINGS)
    beauty, alternate, matte = (f"{PRODUCTS}/{name}" for name in ("Beauty", "Alternate", "CyclesCryptomatte"))
    assert list(map(str, settings.GetProductsRel().GetTargets())) == [beauty, alternate, matte]
    assert not stage.GetPrimAtPath(f"{PRODUCTS}/product_3"), "Disabled product was authored"

    channels = ("CryptoObject00", "CryptoObject01", "CryptoMaterial00", "CryptoAsset00")
    standard = [f"{VARS}/Beauty", f"{VARS}/Depth"]
    beauty_product = UsdRender.Product.Get(stage, beauty)
    assert list(map(str, beauty_product.GetCameraRel().GetTargets())) == [CAMERA_A]
    assert beauty_product.GetPrim().GetAttribute("driver:parameters:husk:output_colorspace").Get() == "ACES2065-1"
    assert list(map(str, beauty_product.GetOrderedVarsRel().GetTargets())) == standard + [f"{VARS}/{c}" for c in channels]
    alternate_product = UsdRender.Product.Get(stage, alternate)
    assert list(map(str, alternate_product.GetCameraRel().GetTargets())) == [CAMERA_B]
    assert list(map(str, alternate_product.GetOrderedVarsRel().GetTargets())) == standard
    assert alternate_product.GetPrim().GetAttribute("driver:parameters:husk:output_colorspace").Get() == "Raw"
    matte_product = UsdRender.Product.Get(stage, matte)
    assert list(map(str, matte_product.GetOrderedVarsRel().GetTargets())) == [f"{VARS}/CryptoObject00"]
    assert matte_product.GetPrim().GetAttribute("driver:parameters:husk:comment").Get() == "Cycles Solaris Indie QA"
    for channel in channels:
        var = UsdRender.Var.Get(stage, f"{VARS}/{channel}")
        assert var.GetSourceNameAttr().Get() == channel
        assert var.GetDataTypeAttr().Get() == "color4f"
        assert var.GetPrim().GetAttribute("driver:parameters:aov:husk:format").Get() == "color4f"
        assert var.GetPrim().GetAttribute("driver:parameters:aov:husk:output_colorspace").Get() == "data"
    return stage


def run():
    directory = Path(os.environ["CYCLES_SOLARIS_QA_DIR"])
    phase = os.environ["CYCLES_SOLARIS_QA_PHASE"]
    hip = directory / "cycles-solaris.hiplc"
    report = {"phase": phase, "version": hou.applicationVersionString()}
    try:
        assert_indie()
        if phase == "create":
            make_network(directory)
        else:
            assert phase == "reopen", phase
            hou.hipFile.load(str(hip), suppress_save_prompt=True)
        otls = Path(os.environ["CYCLES_SOLARIS_QA_OTLS"]).resolve()
        stage = verify_network(otls)
        if phase == "create":
            assert stage.Flatten().Export(str(directory / "cycles-solaris.usda"))
            hou.hipFile.save(str(hip))
            assert hip.is_file(), hou.hipFile.path()
        assert_indie()
        report.update(passed=True, licence="Indie", gui=True, hip=hip.name)
    except Exception:
        report.update(passed=False, error=traceback.format_exc())
    (directory / f"gui-{phase}.json").write_text(json.dumps(report, indent=2) + "\n")
    hou.exit(exit_code=0 if report["passed"] else 1, suppress_save_prompt=True)


def verify_images(directory):
    """Decode both Husk frames and match matte pixels to their EXR manifests."""
    import numpy as np
    import OpenImageIO as oiio

    def read(path):
        image = oiio.ImageInput.open(str(path))
        assert image is not None, path
        try:
            metadata = {attr.name: attr.value for attr in image.spec().extra_attribs}
            layers = {}
            part = 0
            while image.seek_subimage(part, 0):
                spec = image.spec()
                name = spec.get_string_attribute("oiio:subimagename")
                assert (spec.width, spec.height) == (96, 64), path
                if name.startswith("Crypto"):
                    assert spec.nchannels == 4 and spec.format == oiio.FLOAT, (path, name)
                    assert all(fmt == oiio.FLOAT for fmt in spec.channelformats), (path, name)
                layers[name] = image.read_image(oiio.FLOAT)
                part += 1
            return metadata, layers
        finally:
            image.close()

    def check_ids(metadata, layers, kind, expected):
        key = next(key.removesuffix("name") for key, value in metadata.items()
                   if key.startswith("cryptomatte/") and key.endswith("/name") and value == kind)
        assert metadata[key + "hash"] == "MurmurHash3_32"
        assert metadata[key + "conversion"] == "uint32_to_float32"
        manifest = json.loads(metadata[key + "manifest"])
        pixels = layers[kind + "00"]
        for name in expected:
            bits = int(manifest[name], 16)
            if ((bits >> 23) & 0xff) in (0, 0xff):
                bits ^= 1 << 23
            assert np.any((pixels[..., 0].view(np.uint32) == bits) & (pixels[..., 1] > 0.5)), (kind, name)

    result = {}
    for frame in (1001, 1002):
        beauty_meta, beauty = read(directory / f"Beauty.{frame}.exr")
        _, alternate = read(directory / f"Alternate.{frame}.exr")
        matte_meta, matte = read(directory / f"Mattes.{frame}.exr")
        assert list(beauty) == ["C", "Pz", "CryptoObject00", "CryptoObject01", "CryptoMaterial00", "CryptoAsset00"]
        assert list(alternate) == ["C", "Pz"]
        assert list(matte) == ["CryptoObject00"]
        for color in (beauty["C"], alternate["C"]):
            assert np.isfinite(color).all() and np.max(color[..., :3]) > 0.1
        difference = float(np.abs(beauty["C"][..., 3] - alternate["C"][..., 3]).mean())
        assert difference > 0.05, difference
        objects = [f"/World/Asset{name}/Sphere" for name in "AB"]
        check_ids(beauty_meta, beauty, "CryptoObject", objects)
        check_ids(beauty_meta, beauty, "CryptoMaterial", [f"/World/Material{name}" for name in "AB"])
        check_ids(beauty_meta, beauty, "CryptoAsset", [f"/World/Asset{name}" for name in "AB"])
        check_ids(matte_meta, matte, "CryptoObject", objects)
        assert matte_meta["comment"] == "Cycles Solaris Indie QA"
        result[str(frame)] = {"camera_alpha_mean_difference": difference, "matte_manifests_match_pixels": True}
    return result
