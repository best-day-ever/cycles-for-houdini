"""Build the Flatten COP Textures LOP as an installable digital asset.

Cycles cannot read Houdini's `op:` texture paths - neither the USD asset
resolver nor Houdini's own image layer will hand a third party the pixels of a
COP, both were measured returning nothing - so the COPs have to be cooked to
files first. This wraps that pre-pass in a node you drop in front of your
render node instead of pasting code into a Python LOP.

Run with hython:

    hython tools/build_hda.py
"""

import os
import tempfile

import hou

from build_aovs_hda import HDA_DIR, require_authoring_license, save_asset

HDA = os.path.join(HDA_DIR, "hdcycles_flatten_op_textures.hda")

TYPE_NAME = "hdcycles::flatten_op_textures::1.0"
LABEL = "Flatten COP Textures"

# The node body. Kept tiny on purpose: the real work lives in the module next
# to this file, so fixing it does not mean rebuilding the asset.
SCRIPT = '''import flatten_op_textures

node = hou.pwd()
directory = node.evalParm("outputdir") or None
count = flatten_op_textures.run(node, directory)
if count:
    node.setComment("%d texture(s) flattened" % count)
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
'''


def build(scratch):
    stage = hou.node("/stage")
    subnet = stage.createNode("subnet", "flatten_tmp")

    script = subnet.createNode("pythonscript")
    script.parm("python").set(SCRIPT)
    script.setFirstInput(subnet.indirectInputs()[0])
    script.setDisplayFlag(True)

    asset = subnet.createDigitalAsset(
        name=TYPE_NAME,
        hda_file_name=os.path.join(scratch, "build.hda"),
        description=LABEL,
        min_num_inputs=1,
        max_num_inputs=1,
        ignore_external_references=True,
    )

    definition = asset.type().definition()

    # A single visible parameter: where the cooked images go.
    group = hou.ParmTemplateGroup()
    group.append(hou.StringParmTemplate(
        "outputdir",
        "Output Directory",
        1,
        default_value=("$HIP/cop_textures",),
        string_type=hou.stringParmType.FileReference,
        file_type=hou.fileType.Directory,
        help="Where cooked COP images are written. Left empty, the system "
             "temporary directory is used."))
    definition.setParmTemplateGroup(group)

    save_asset(asset, HDA)


def main():
    require_authoring_license()
    with tempfile.TemporaryDirectory(prefix="hda_") as scratch:
        build(scratch)


if __name__ == "__main__":
    main()
