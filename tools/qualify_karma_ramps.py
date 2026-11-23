"""Check legacy Karma and Houdini MaterialX ramps through standalone Cycles."""

import argparse
from pathlib import Path
import tempfile

import hou
import numpy as np

from bench_usdtex import mtlx_scene
from qualify_legacy_usd import render


MATERIAL = "/world/Materials/M"


def shader(name, identifier, parameters, output):
    return (
        f'def Shader "{name}" {{\n'
        f'uniform token info:id = "{identifier}"\n'
        f'{parameters}\n'
        f'{output} outputs:out\n}}\n'
    )


def textureless_scene(nodes, output):
    text = mtlx_scene("unused.exr", "")
    start = text.index('            def Shader "img"')
    end = text.index('            def Shader "st"')
    return (text[:start] + nodes + text[end:]).replace(
        f"{MATERIAL}/img.outputs:out", f"{MATERIAL}/{output}.outputs:out"
    )


def karma_ramp(t, basis, keys, values, scalar):
    usd_type = "float" if scalar else "color3f"
    prefix = "f" if scalar else "v"
    nodes = shader("coordinate", "ND_constant_float", f"float inputs:value = {t}", "float")
    attrs = f"float inputs:t.connect = <{MATERIAL}/coordinate.outputs:out>"
    expected = t
    if basis:
        modes = ", ".join(f'"{name}"' for name in basis)
        contents = ", ".join(str(value) for value in values)
        attrs += (
            f"\nfloat[] inputs:{prefix}keys = [{', '.join(map(str, keys))}]"
            f"\n{usd_type}[] inputs:{prefix}values = [{contents}]"
            f"\ntoken[] inputs:{prefix}basis = [{modes}]"
        )
        hom = {
            "linear": hou.rampBasis.Linear,
            "constant": hou.rampBasis.Constant,
            "catmull-rom": hou.rampBasis.CatmullRom,
            "bezier": hou.rampBasis.Bezier,
        }
        expected = hou.Ramp(tuple(hom[mode] for mode in basis), keys, values).lookup(t)

    nodes += shader("ramp", "kma_rampconst_float" if scalar else "kma_rampconst_color",
                    attrs, usd_type)
    if scalar:
        nodes += shader("rgb", "ND_convert_float_color3",
                        f"float inputs:in.connect = <{MATERIAL}/ramp.outputs:out>", "color3f")
        return textureless_scene(nodes, "rgb"), [expected] * 3
    return textureless_scene(nodes, "ramp"), expected


def materialx_ramp():
    nodes = shader("count", "ND_constant_float", "float inputs:value = 3", "float")
    count = f"float inputs:num.connect = <{MATERIAL}/count.outputs:out>"
    nodes += shader("keyed", "ND_hinvlinear_float", count + """
float inputs:key0 = 0.1
float inputs:key1 = 0.4
float inputs:key2 = 0.9
float inputs:ramp = 0.55
""", "float")
    nodes += shader("ramp", "ND_huniformramp_color3", count + f"""
float inputs:ramp.connect = <{MATERIAL}/keyed.outputs:out>
color3f inputs:value0 = (0.8, 0.0, 0.6)
color3f inputs:value1 = (0.2, 0.4, 0.6)
color3f inputs:value2 = (0.5, 0.1, 0.0)
""", "color3f")
    return textureless_scene(nodes, "ramp"), [0.29, 0.31, 0.42]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--device", default="CPU", choices=("CPU", "OPTIX", "CUDA", "METAL"))
    args = parser.parse_args()
    cases = {
        "default": karma_ramp(0.35, (), (), (), True),
        "color": karma_ramp(0.6, ("linear",) * 3, (0.0, 0.3, 1.0),
                            ((0.2, 0.0, 0.05), (0.1, 0.7, 0.4), (0.8, 0.4, 0.1)), False),
        "mixed": karma_ramp(0.43, ("constant", "catmull-rom", "linear"), (0.0, 0.25, 1.0),
                            (0.1, 0.75, 0.25), True),
        "bezier": karma_ramp(0.74, ("bezier",) * 4, (0.0, 0.718, 0.964, 1.0),
                             (0.0, 0.0157, 0.499, 1.0), True),
        "keyed-materialx": materialx_ramp(),
    }
    passed = True
    with tempfile.TemporaryDirectory(prefix="cycles-karma-ramps-") as directory:
        for name, (scene, expected) in cases.items():
            image = render(args.runtime.resolve(), Path(directory), name, scene, args.device)
            actual = image[24:40, 24:40].mean(axis=(0, 1))
            error = np.abs(actual - expected).max()
            passed &= error < 0.015
            print(f"{name}: {actual.round(5)} expected {np.round(expected, 5)}, error {error:.6f}",
                  flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
