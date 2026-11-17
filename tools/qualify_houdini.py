"""Author a small animated Solaris stage in a disposable Houdini session."""
import argparse
import json
from pathlib import Path


def author(stage):
    from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdRender, UsdShade
    UsdGeom.Xform.Define(stage, '/World')
    # Deliberately first in traversal: a reader that ignores the selected camera
    # renders an empty picture.
    wrong = UsdGeom.Camera.Define(stage, '/World/aaa_wrong_camera')
    wrong.AddTranslateOp().Set(Gf.Vec3d(100, 0, 7))
    camera = UsdGeom.Camera.Define(stage, '/World/camera')
    camera.AddTranslateOp().Set(Gf.Vec3d(0, 0, 9))
    camera.CreateFocalLengthAttr(38)
    dome = UsdLux.DomeLight.Define(stage, '/World/light')
    dome.CreateIntensityAttr(.7)
    material = UsdShade.Material.Define(stage, '/World/material')
    shader = UsdShade.Shader.Define(stage, '/World/material/surface')
    shader.CreateIdAttr('cycles_principled_bsdf')
    shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.4)
    color = shader.CreateInput('base_color', Sdf.ValueTypeNames.Color3f)
    color.Set(Gf.Vec3f(.7, .015, .01))
    color.Set(Gf.Vec3f(.7, .015, .01), 1)
    color.Set(Gf.Vec3f(.01, .7, .015), 2)
    shader.CreateOutput('BSDF', Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput('cycles').ConnectToSource(shader.ConnectableAPI(), 'BSDF')
    sphere = UsdGeom.Sphere.Define(stage, '/World/native')
    sphere.CreateRadiusAttr(.9)
    sphere.AddTranslateOp().Set(Gf.Vec3d(-1.2, 0, 0))
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    mtlx_material = UsdShade.Material.Define(stage, '/World/materialx')
    mtlx = UsdShade.Shader.Define(stage, '/World/materialx/surface')
    mtlx.CreateIdAttr('ND_standard_surface_surfaceshader')
    mtlx.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.025, .1, .7))
    mtlx.CreateInput('specular_roughness', Sdf.ValueTypeNames.Float).Set(.35)
    mtlx.CreateOutput('out', Sdf.ValueTypeNames.Token)
    mtlx_material.CreateSurfaceOutput('mtlx').ConnectToSource(mtlx.ConnectableAPI(), 'out')
    cube = UsdGeom.Cube.Define(stage, '/World/mtlx')
    cube.CreateSizeAttr(1.5)
    move = cube.AddTranslateOp()
    move.Set(Gf.Vec3d(1.2, 0, 0))
    move.Set(Gf.Vec3d(1.2, 0, 0), 1)
    move.Set(Gf.Vec3d(1.5, .3, 0), 2)
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mtlx_material)
    settings = UsdRender.Settings.Define(stage, '/Render/settings')
    settings.CreateCameraRel().SetTargets([camera.GetPath()])
    settings.CreateResolutionAttr(Gf.Vec2i(96, 64))
    settings.GetPrim().CreateAttribute('cycles:samples', Sdf.ValueTypeNames.Int).Set(16)
    var = UsdRender.Var.Define(stage, '/Render/beauty')
    var.CreateDataTypeAttr('color4f')
    var.CreateSourceNameAttr('color')
    var.CreateSourceTypeAttr('raw')
    var.GetPrim().CreateAttribute('driver:parameters:aov:husk:name', Sdf.ValueTypeNames.String).Set('color')
    var.GetPrim().CreateAttribute('driver:parameters:aov:husk:format', Sdf.ValueTypeNames.Token).Set('color4f')
    product = UsdRender.Product.Define(stage, '/Render/product')
    product.CreateProductTypeAttr('raster')
    product.CreateProductNameAttr('beauty.exr')
    product.CreateOrderedVarsRel().SetTargets([var.GetPath()])
    settings.CreateProductsRel().SetTargets([product.GetPath()])


def main():
    import hou
    from pxr import Usd
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--require-plugin', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.require_plugin:
        from pxr import Plug, Sdr
        assert Plug.Registry().GetPluginWithName('hdCycles').Load()
        assert Sdr.Registry().GetShaderNodeByIdentifier('cycles_principled_bsdf')
        assert 'HdCyclesPluginGPU' in hou.lop.availableRendererNames()
        library = hou.node('/stage').createNode('materiallibrary', 'cycles_native_vop')
        vop = library.createNode('cycles_principled_bsdf')
        vop.setMaterialFlag(True)
        assert library.stage() and not library.errors(), library.errors()
    node = hou.node('/stage').createNode('pythonscript', 'cycles_qualification')
    code = ('import hou,importlib.util\n'
            f's=importlib.util.spec_from_file_location("cycles_fixture",{str(Path(__file__).resolve())!r})\n'
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
            'm.author(hou.pwd().editableStage())\n')
    node.parm('python').set(code)
    stage = node.stage()
    assert stage and not node.errors(), node.errors()
    path = args.output_dir / 'houdini.usda'
    stage.Export(str(path))
    # Layer metadata belongs on the exported root, not Houdini's transient LOP
    # edit layer. All geometry, shaders and time samples above are authored by LOPs.
    exported = Usd.Stage.Open(str(path))
    exported.SetStartTimeCode(1)
    exported.SetEndTimeCode(2)
    exported.SetTimeCodesPerSecond(24)
    exported.SetMetadata('renderSettingsPrimPath', '/Render/settings')
    exported.SetMetadata('metersPerUnit', 1.0)
    exported.SetMetadata('upAxis', 'Y')
    exported.GetRootLayer().Save()
    hou.hipFile.save(str(args.output_dir / 'houdini.hip'))
    receipt = dict(houdini=hou.applicationVersionString(), usd=Usd.GetVersion(),
                   scene=str(path), renderers=hou.lop.availableRendererNames())
    (args.output_dir / 'authoring.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
