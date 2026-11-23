"""Build the Render Products and Cryptomatte LOPs using licensed hython."""

import os
from pathlib import Path
import shutil
import sys
import tempfile

import hou

from build_aovs_hda import TOOL, require_authoring_license, save_asset


RENDER_VAR_MENU = """import loputils
inputs = kwargs['node'].inputs()
return loputils.createRenderVarPrimMenu(inputs[0].stage()) if inputs and inputs[0] else []
"""


def string(name, label, default="", **kwargs):
    return hou.StringParmTemplate(name, label, 1, default_value=(default,), **kwargs)


def prim_field(name, label, default="", multiple=False, **kwargs):
    return string(name, label, default, tags={
        "sidefx::usdpathtype": "prim",
        "script_action": f"import loputils; loputils.selectPrimsInParm(kwargs, {multiple})",
        "script_action_icon": "BUTTONS_reselect",
    }, **kwargs)


def render_vars(name, label, **kwargs):
    return prim_field(name, label, multiple=True, item_generator_script=RENDER_VAR_MENU,
                      item_generator_script_language=hou.scriptLanguage.Python,
                      menu_type=hou.menuType.StringToggle, **kwargs)


def colorspace(name, label, **kwargs):
    return string(name, label,
                  item_generator_script="import toolutils\nreturn toolutils.ocioColorSpaceMenuList()",
                  item_generator_script_language=hou.scriptLanguage.Python, **kwargs)


def picture(name, default="", **kwargs):
    return string(name, "Output Picture", default, string_type=hou.stringParmType.FileReference,
                  file_type=hou.fileType.Image, **kwargs)


def settings_field():
    return prim_field("settings", "Render Settings",
                      help="Leave empty to use the stage selection or Cycles Render Settings.")


def products_parameters():
    parms = hou.ParmTemplateGroup()
    parms.append(prim_field("parentpath", "Product Parent", "/Render/Products"))
    parms.append(settings_field())
    parms.append(hou.FolderParmTemplate("defaults", "Common Settings", folder_type=hou.folderType.Simple,
                                      parm_templates=(render_vars("orderedvars", "Ordered Render Vars"),
                                                      prim_field("camera", "Camera"),
                                                      colorspace("colorspace", "Output Colorspace"))))
    disabled = {hou.parmCondType.DisableWhen: "{ enable# == 0 }"}
    parms.append(hou.FolderParmTemplate(
        "products", "Render Products", default_value=1, folder_type=hou.folderType.MultiparmBlock,
        parm_templates=(
            hou.ToggleParmTemplate("enable#", "Enable", default_value=True),
            string("name#", "Product Name", conditionals=disabled, help="Defaults to product_N."),
            picture("picture#", conditionals=disabled,
                    help="Defaults to $HIP/render/<product>.%04d.exr. Use %04d for the frame number."),
            render_vars("orderedvars#", "Ordered Render Vars", conditionals=disabled,
                        help="Leave empty to use the common Render Vars. Enter paths in output order."),
            prim_field("camera#", "Camera", conditionals=disabled,
                       help="Leave empty to use the common camera or the Render Settings camera."),
            hou.MenuParmTemplate("csmode#", "Colorspace Operation",
                                 ("inherit", "set", "if_missing", "if_authored", "block"),
                                 ("Use Common Setting", "Set", "Set If Missing", "Set If Authored", "Block"),
                                 conditionals=disabled),
            colorspace("colorspace#", "Output Colorspace", conditionals={
                hou.parmCondType.DisableWhen: "{ enable# == 0 } { csmode# == inherit } { csmode# == block }"}),
        ),
    ))
    return parms


def cryptomatte_parameters():
    parms = hou.ParmTemplateGroup()
    parms.append(hou.MenuParmTemplate("mode", "Output", ("append", "separate"),
                                     ("Add to Render Products", "Create Separate EXR")))
    parms.append(settings_field())
    parms.append(prim_field("products", "Render Products", multiple=True, conditionals={
        hou.parmCondType.HideWhen: "{ mode != append }"},
        help="Leave empty to use the products on the selected Render Settings."))
    separate = {hou.parmCondType.HideWhen: "{ mode != separate }"}
    parms.append(prim_field("productpath", "Matte Product", "/Render/Products/CyclesCryptomatte",
                           conditionals=separate))
    parms.append(picture("picture", "$HIP/render/cryptomatte.%04d.exr", conditionals=separate))
    parms.append(string("artist", "EXR Artist", conditionals=separate))
    parms.append(string("comment", "EXR Comment", conditionals=separate))
    parms.append(prim_field("varspath", "Render Vars Scope", help="Defaults to Vars beside the product."))
    for kind, label, ranks in (("object", "Object", 6), ("material", "Material", 0), ("asset", "Asset", 0)):
        parms.append(hou.IntParmTemplate(f"{kind}ranks", f"{label} Ranks", 1, default_value=(ranks,),
                                        min=0, max=16, min_is_strict=True, max_is_strict=True,
                                        help="Zero disables this layer. Each RGBA pass holds two ranks."))
    return parms


def build_asset(directory, name, label, icon, parameters, cook_function):
    type_name = f"hdcycles::{name}::1.0"
    subnet = hou.node("/stage").createNode("subnet", name)
    python = subnet.createNode("pythonscript", "author_usd")
    python.parm("python").set(f"import cycles_solaris\ncycles_solaris.{cook_function}(hou.pwd())\n")
    python.setFirstInput(subnet.indirectInputs()[0])
    python.setDisplayFlag(True)
    subnet.node("output0").setFirstInput(python)
    with tempfile.TemporaryDirectory(prefix="cycles-lop-") as scratch:
        asset = subnet.createDigitalAsset(type_name, str(Path(scratch) / "build.hda"),
                                         description=label, min_num_inputs=1, max_num_inputs=1,
                                         ignore_external_references=True)
        definition = asset.type().definition()
        definition.setParmTemplateGroup(parameters)
        definition.setIcon(icon)
        definition.addSection("Tools.shelf", TOOL)
        require_authoring_license()
        save_asset(asset, str(directory / f"hd{name}.hda"))


def main():
    require_authoring_license()
    otls = Path(os.environ["CYCLES_HDA_DIR"])
    libraries = otls.parent / f"python{sys.version_info.major}.{sys.version_info.minor}libs"
    libraries.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("cycles_solaris.py"), libraries / "cycles_solaris.py")
    build_asset(otls, "cycles_render_products", "Cycles Render Products", "LOP_renderproduct",
                products_parameters(), "products_from_node")
    build_asset(otls, "cycles_cryptomatte", "Cycles Cryptomatte", "LOP_karmacryptomatte",
                cryptomatte_parameters(), "cryptomatte_from_node")


if __name__ == "__main__":
    main()
