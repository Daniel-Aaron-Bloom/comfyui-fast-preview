"""Import the repo the way ComfyUI does (as a package), against stand-ins for
the two ComfyUI modules it needs. Each call gives a fresh copy, so a test never
sees another's singletons."""

import importlib.util
import os
import sys
import tempfile
import types
from types import SimpleNamespace

import numpy as np
import torch
from aiohttp import web

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load(client_id="client-1"):
    for name in [m for m in sys.modules if m == "fastpreview" or m.startswith("fastpreview.")]:
        del sys.modules[name]
    temp = tempfile.mkdtemp(prefix="fastpreview-test-")

    server = types.ModuleType("server")

    class PromptServer:
        instance = SimpleNamespace(routes=web.RouteTableDef(), loop=None, client_id=client_id)

    server.PromptServer = PromptServer
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: temp
    sys.modules["server"] = server
    sys.modules["folder_paths"] = folder_paths

    spec = importlib.util.spec_from_file_location(
        "fastpreview", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["fastpreview"] = pkg
    spec.loader.exec_module(pkg)
    pkg.temp_dir = temp
    pkg.server = PromptServer.instance
    return pkg


def split_image(w=96, h=64, batch=1, left=(1.0, 0.0, 0.0), right=(0.0, 0.0, 1.0), channels=3):
    """Left half one flat colour, right half another: orientation and colour
    survive any resize, so a transposed or channel-swapped result is obvious."""
    x = torch.zeros(batch, h, w, channels)
    x[:, :, : w // 2, :3] = torch.tensor(left)
    x[:, :, w // 2:, :3] = torch.tensor(right)
    if channels == 4:
        x[..., 3] = 1.0
    return x


def stock_u8(x):
    """What SaveImage does to a [H, W, C] tensor, verbatim."""
    return np.clip(255.0 * x.cpu().numpy(), 0, 255).astype(np.uint8)
