"""Inventory authored Karma and MaterialX features on a composed render stage.

Run with the USD Python shipped with the writing Houdini version for its schema
plugins. The inventory describes authored inputs. Renderer support requires
separate qualification.
"""

import argparse
from collections import Counter
from itertools import chain
import json
import math

from pxr import Sdf, Tf, Usd, UsdGeom, UsdLux, UsdRender, UsdShade


def authored_names(prim, prefix):
    return sorted(
        a.GetName()
        for a in prim.GetAuthoredAttributes()
        if a.GetName().startswith(prefix) and (a.HasAuthoredValueOpinion() or a.GetConnections())
    )


def shader_ids(stage, output, time):
    nodes = set()
    visited = set()
    pending = list(output.GetConnections())
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        attr = stage.GetAttributeAtPath(path)
        if not attr:
            continue
        pending.extend(attr.GetConnections())
        shader = UsdShade.Shader(attr.GetPrim())
        if not shader or shader.GetPath() in nodes:
            continue
        nodes.add(shader.GetPath())
        for param in shader.GetInputs():
            pending.extend(param.GetAttr().GetConnections())
    ids = [UsdShade.Shader.Get(stage, p).GetIdAttr().Get(time) for p in nodes]
    return sorted(set(str(identifier) if identifier else "(no info:id)" for identifier in ids))


def terminal_contract(stage, prim, time):
    outputs = UsdShade.Material(prim).GetOutputs()
    return {
        output.GetBaseName(): shader_ids(stage, output.GetAttr(), time)
        for output in outputs
        if output.GetAttr().GetConnections()
    }


def output_contract(stage, settings, time):
    products = []
    for path in settings.GetProductsRel().GetForwardedTargets():
        product = UsdRender.Product.Get(stage, path)
        if not product:
            products.append({"path": str(path), "missing": True})
            continue
        variables = []
        for var_path in product.GetOrderedVarsRel().GetForwardedTargets():
            var = UsdRender.Var.Get(stage, var_path)
            variables.append({
                "name": var_path.name,
                "type": var.GetDataTypeAttr().Get(time),
                "source_type": var.GetSourceTypeAttr().Get(time),
                "source_name": var.GetSourceNameAttr().Get(time),
            } if var else {"name": var_path.name, "missing": True})
        products.append({
            "path": str(path),
            "type": product.GetProductTypeAttr().Get(time),
            "camera_override": [str(p) for p in product.GetCameraRel().GetForwardedTargets()],
            "ordered_vars": variables,
            "authored_properties": authored_names(product.GetPrim(), ""),
        })
    return products


def inventory(stage, time, settings_path):
    settings = (UsdRender.Settings.Get(stage, settings_path) if settings_path else
                UsdRender.Settings.GetStageRenderSettings(stage))
    if not settings:
        raise ValueError("No active UsdRenderSettings; use --settings with a prim path")

    materials = {}
    terminal_counts = Counter()
    shader_types = set()
    types = Counter()
    object_properties = Counter()
    material_bindings = Counter()
    lights = []
    assets = Counter()
    prims = chain(stage.Traverse(), *(Usd.PrimRange(p) for p in stage.GetPrototypes()))
    for prim in prims:
        types[prim.GetTypeName()] += 1
        object_properties.update(authored_names(prim, "primvars:karma:object:"))
        material_bindings.update(
            relation.GetName() for relation in prim.GetAuthoredRelationships()
            if relation.GetName().startswith("material:binding")
        )
        if prim.IsA(UsdShade.Material):
            terminals = terminal_contract(stage, prim, time)
            materials[str(prim.GetPath())] = terminals
            terminal_counts.update(terminals.keys())
            for ids in terminals.values():
                shader_types.update(ids)
        if prim.HasAPI(UsdLux.LightAPI):
            api = UsdLux.LightAPI(prim)
            links = api.GetLightLinkCollectionAPI()
            shadows = api.GetShadowLinkCollectionAPI()
            lights.append({
                "path": str(prim.GetPath()),
                "type": prim.GetTypeName(),
                "inputs": authored_names(prim, "inputs:"),
                "filter_count": len(api.GetFiltersRel().GetForwardedTargets()),
                "light_link_includes": len(links.GetIncludesRel().GetForwardedTargets()),
                "light_link_excludes": len(links.GetExcludesRel().GetForwardedTargets()),
                "shadow_link_includes": len(shadows.GetIncludesRel().GetForwardedTargets()),
                "shadow_link_excludes": len(shadows.GetExcludesRel().GetForwardedTargets()),
            })
        for attr in prim.GetAuthoredAttributes():
            if attr.GetTypeName() != Sdf.ValueTypeNames.Asset:
                continue
            asset = attr.Get(time)
            if asset and asset.path:
                scheme = asset.path.split(":", 1)[0] if ":" in asset.path else ""
                kind = scheme if scheme in {"op", "opdef"} else "file"
                assets[(kind, bool(asset.resolvedPath))] += 1

    return {
        "time_code": time.GetValue(),
        "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
        "prim_types": dict(types),
        "materials": materials,
        "material_binding_relationships": dict(material_bindings),
        "terminal_counts": dict(terminal_counts),
        "reachable_shader_ids": sorted(shader_types),
        "geometry_properties": dict(object_properties),
        "lights": lights,
        "authored_asset_counts": [
            {"type": kind, "resolved": resolved, "count": count}
            for (kind, resolved), count in sorted(assets.items())
        ],
        "render_settings": {
            "path": str(settings.GetPath()),
            "camera": [str(p) for p in settings.GetCameraRel().GetForwardedTargets()],
            "authored_properties": authored_names(settings.GetPrim(), ""),
            "included_purposes": list(settings.GetIncludedPurposesAttr().Get(time) or []),
            "material_binding_purposes": list(settings.GetMaterialBindingPurposesAttr().Get(time)
                                              or []),
            "products": output_contract(stage, settings, time),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    parser.add_argument("--frame", type=float)
    parser.add_argument("--settings", help="Explicit UsdRenderSettings prim path")
    args = parser.parse_args()
    try:
        stage = Usd.Stage.Open(args.stage)
        if not stage:
            raise ValueError(f"Could not open USD stage: {args.stage}")
        frame = args.frame if args.frame is not None else stage.GetStartTimeCode()
        if not math.isfinite(frame):
            raise ValueError("Frame must be finite")
        report = inventory(stage, Usd.TimeCode(frame), args.settings)
    except (Tf.ErrorException, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
