"""USD authoring for the Cycles Render Products and Cryptomatte LOPs."""

import hou
from pxr import Sdf, Tf, UsdRender

import cycles_aovs


DEFAULT_SETTINGS = "/Render/CyclesRenderSettings"
COLORSPACE = "driver:parameters:husk:output_colorspace"
MATTE_KINDS = (("object", "CryptoObject"), ("material", "CryptoMaterial"), ("asset", "CryptoAsset"))


def prim_path(value, label):
    if not Sdf.Path.IsValidPathString(value):
        raise hou.NodeError(f"{label} must be an absolute USD prim path: {value!r}")
    path = Sdf.Path(value)
    if not path.IsAbsolutePath() or not path.IsPrimPath():
        raise hou.NodeError(f"{label} must be an absolute USD prim path: {value!r}")
    return path


def settings_for(stage, asset):
    explicit = asset.evalParm("settings").strip()
    if explicit:
        settings = UsdRender.Settings.Get(stage, prim_path(explicit, "Render Settings"))
        if not settings:
            raise hou.NodeError(f"No Render Settings prim at {explicit}.")
        return settings
    selected = stage.GetMetadata("renderSettingsPrimPath") or DEFAULT_SETTINGS
    return UsdRender.Settings.Get(stage, selected)


def append_targets(relationship, paths):
    relationship.SetTargets(list(dict.fromkeys([*relationship.GetTargets(), *paths])))


def matching_paths(stage, asset, pattern, schema, allow_new=False):
    terms = pattern.split()
    if any(operator in pattern for operator in "&|^()") or {"+", "-"}.intersection(terms):
        terms = [pattern]
    paths = []
    for term in terms:
        for path in hou.LopSelectionRule(term).expandedPaths(
                stage=stage, lopnode=asset, fallback_to_new_paths=allow_new):
            prim = stage.GetPrimAtPath(path)
            if (prim and prim.IsA(schema)) or (allow_new and not prim and path.IsPrimPath()):
                paths.append(path)
    return list(dict.fromkeys(paths))


def render_product(stage, path, picture, var_paths, camera=""):
    existing = stage.GetPrimAtPath(path)
    if existing and not existing.IsA(UsdRender.Product):
        raise hou.NodeError(f"A prim at {path} already exists and is not a Render Product.")
    product = UsdRender.Product.Define(stage, path)
    product.GetPrim().AddAppliedSchema("HuskRenderProductAPI")
    product.CreateProductTypeAttr(UsdRender.Tokens.raster)
    product.CreateProductNameAttr(picture)
    product.CreateOrderedVarsRel().SetTargets(var_paths)
    if camera:
        product.CreateCameraRel().SetTargets([prim_path(camera, "Camera")])
    return product


def output_colorspace(prim, mode, value):
    attr = prim.GetAttribute(COLORSPACE)
    has_value = attr and attr.HasAuthoredValue()
    if (mode == "if_missing" and has_value) or (mode == "if_authored" and not has_value):
        return
    if mode == "block":
        prim.CreateAttribute(COLORSPACE, Sdf.ValueTypeNames.String, custom=False).Block()
    elif value:
        prim.CreateAttribute(COLORSPACE, Sdf.ValueTypeNames.String, custom=False).Set(value)


def products_from_node(node):
    asset = node.parent()
    stage = node.editableStage()
    parent = prim_path(asset.evalParm("parentpath").strip(), "Product Parent")
    settings = settings_for(stage, asset)
    common_vars = asset.evalParm("orderedvars").strip()
    common_camera = asset.evalParm("camera").strip()
    common_colorspace = asset.evalParm("colorspace").strip()
    products = asset.parm("products")
    start = products.multiParmStartOffset()
    paths = []
    for index in range(start, start + products.evalAsInt()):
        if not asset.evalParm(f"enable{index}"):
            continue
        name = asset.evalParm(f"name{index}").strip() or f"product_{index}"
        if not Tf.IsValidIdentifier(name):
            raise hou.NodeError(f"Product {index} needs a valid USD prim name: {name!r}")
        path = parent.AppendChild(name)
        if path in paths:
            raise hou.NodeError(f"Product name {name!r} occurs more than once.")
        paths.append(path)
        picture = asset.evalParm(f"picture{index}").strip() or hou.text.expandString(
            f"$HIP/render/{name}.%04d.exr")
        pattern = asset.evalParm(f"orderedvars{index}").strip() or common_vars
        var_paths = matching_paths(stage, asset, pattern, UsdRender.Var, allow_new=True)
        camera = asset.evalParm(f"camera{index}").strip() or common_camera
        product = render_product(stage, path, picture, var_paths, camera)
        mode = asset.parm(f"csmode{index}").evalAsString()
        value = common_colorspace if mode == "inherit" else asset.evalParm(f"colorspace{index}").strip()
        output_colorspace(product.GetPrim(), mode, value)
    if settings and paths:
        append_targets(settings.CreateProductsRel(), paths)


def matte_paths(stage, location, asset):
    paths = []
    for kind, prefix in MATTE_KINDS:
        ranks = asset.evalParm(f"{kind}ranks")
        for index in range((ranks + 1) // 2):
            name = f"{prefix}{index:02d}"
            # Use the delegate's fixed pass and channel names to match its EXR manifest.
            path = cycles_aovs._define_var(stage, str(location), name, name, "color4f", "color4f")
            prim = stage.GetPrimAtPath(path)
            prim.AddAppliedSchema("HuskRenderVarAPI")
            prim.CreateAttribute("driver:parameters:aov:husk:name", Sdf.ValueTypeNames.String).Set(name)
            prim.CreateAttribute("driver:parameters:aov:husk:format", Sdf.ValueTypeNames.Token).Set("color4f")
            for direction in ("source", "output"):
                prim.CreateAttribute(f"driver:parameters:aov:husk:{direction}_colorspace",
                                     Sdf.ValueTypeNames.String).Set("data")
            paths.append(path)
    return paths


def cryptomatte_from_node(node):
    asset = node.parent()
    stage = node.editableStage()
    settings = settings_for(stage, asset)
    location = asset.evalParm("varspath").strip()
    if asset.evalParm("mode") == 0:
        pattern = asset.evalParm("products").strip()
        if pattern:
            paths = matching_paths(stage, asset, pattern, UsdRender.Product)
            if not paths:
                raise hou.NodeError(f"No Render Products match {pattern!r}.")
        else:
            paths = settings.GetProductsRel().GetForwardedTargets() if settings else []
        for path in paths:
            product = UsdRender.Product.Get(stage, path)
            if not product:
                continue
            scope = prim_path(location, "Render Vars") if location else path.GetParentPath().AppendChild("Vars")
            vars_for_product = matte_paths(stage, scope, asset)
            if vars_for_product:
                append_targets(product.CreateOrderedVarsRel(), vars_for_product)
        return

    path = prim_path(asset.evalParm("productpath").strip(), "Matte Product")
    scope = prim_path(location, "Render Vars") if location else path.GetParentPath().AppendChild("Vars")
    vars_for_product = matte_paths(stage, scope, asset)
    if not vars_for_product:
        return
    product = render_product(stage, path, asset.evalParm("picture"), vars_for_product)
    prim = product.GetPrim()
    for key in ("artist", "comment"):
        value = asset.evalParm(key).strip()
        if value:
            prim.CreateAttribute(f"driver:parameters:husk:{key}", Sdf.ValueTypeNames.String).Set(value)
    if settings:
        append_targets(settings.CreateProductsRel(), [path])
