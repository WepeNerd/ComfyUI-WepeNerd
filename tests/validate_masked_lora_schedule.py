"""Opt-in, fixed-seed native Krea2 generation evidence. No downloads or installs.

Pass local checkpoint, text encoder, VAE, adapter and an output directory.
Each run writes its exact settings, runtime counters, timings and decoded PNG.
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path
import platform
import subprocess
import sys
import time
import types

import numpy as np
from PIL import Image, ImageDraw
import torch

ROOT = Path(__file__).resolve().parents[1]
COMFY = ROOT.parents[1]
sys.path.insert(0, str(COMFY))
import comfy.cli_args
comfy.cli_args.args.disable_dynamic_vram = True
import comfy.model_management
import comfy.patcher_extension as pe
import comfy.sample
import comfy.samplers
import comfy.sd
import comfy.utils
import folder_paths

package = types.ModuleType("wepenerd_schedule_validation")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from wepenerd_schedule_validation import masked_lora_node as ml
from wepenerd_schedule_validation.masked_lora_runtime import hash_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint", "text-encoder", "vae", "adapter", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--steps", type=int, default=16)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--seed", type=int, default=25092026)
    parser.add_argument("--baseline-only", action="store_true", help="Render the same scene at zero adapter strength for artifact diagnosis.")
    args = parser.parse_args()
    strength = 0. if args.baseline_only else .8
    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG, handlers=[logging.FileHandler(args.output / "debug.log", encoding="utf-8")], force=True)
    folder_paths.set_input_directory(str(args.output / "input"))
    folder_paths.add_model_folder_path("loras", str(args.adapter.parent))
    evidence = dict(status="running", python=platform.python_version(), torch=torch.__version__, cuda=torch.version.cuda,
                    gpu=torch.cuda.get_device_name(), comfy_commit=subprocess.check_output(["git", "-c", f"safe.directory={COMFY.as_posix()}", "rev-parse", "HEAD"], cwd=COMFY, text=True).strip(),
                    seed=args.seed, width=args.width, height=args.height, steps=args.steps, cfg=3., strength=strength,
                    guider="comfy.samplers.CFGGuider; 0=positive, 1=negative", sampler="euler", scheduler="simple", runs=[])
    def save():
        (args.output / "results.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    try:
        evidence["files"] = {}
        for name in ("checkpoint", "text_encoder", "vae", "adapter"):
            path = getattr(args, name)
            digest = hash_file(path)
            evidence["files"][name] = dict(path=str(path), sha256=digest, bytes=path.stat().st_size)
        print("Loading checkpoint and checking the existing format boundary", flush=True)
        model = comfy.sd.load_diffusion_model(str(args.checkpoint))
        mask = torch.linspace(1.5, -.5, args.width).clamp(0, 1).repeat(args.height, 1)
        base, _ = ml.WepeNerdLoadLoraMasked().load(model, args.adapter.name, strength, mask=mask)
        evidence["adapter_layers"] = len(base.object_patches)
        evidence["native_formats"] = list({str((getattr(module, "quant_format", None), getattr(module, "layout_type", None)))
            for name, module in model.model.diffusion_model.named_modules() if ml.SPATIAL_LAYER.fullmatch("diffusion_model." + name + ".weight")})
        save()
        print("Encoding fixed prompts", flush=True)
        prompt = "An 80s fantasy movie still, a stone wizard tower on a green hillside, a winding footpath, distant mountains, cloudy golden afternoon light, richly textured cinematic landscape."
        evidence["prompt"] = prompt
        clip = comfy.sd.load_clip([str(args.text_encoder)], clip_type=comfy.sd.CLIPType.KREA2)
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        negative = clip.encode_from_tokens_scheduled(clip.tokenize(""))
        del clip
        comfy.model_management.unload_all_models()
        latent = torch.zeros(1, 16, args.height // 8, args.width // 8)
        noise = comfy.sample.prepare_noise(latent, args.seed)
        sigmas = comfy.samplers.calculate_sigmas(model.get_model_object("model_sampling"), "simple", args.steps).cpu()
        evidence["sigmas"] = sigmas.tolist()
        sampler = comfy.samplers.sampler_object("euler")
        images = []
        vae = None
        cases = (("baseline", 0., 1., "Both"),) if args.baseline_only else (
            ("full_both", 0., 1., "Both"), ("early_both", 0., .5, "Both"),
            ("late_both", .5, 1., "Both"), ("full_positive", 0., 1., "Positive only"))
        for name, start, end, mode in cases:
            print("Sampling " + name, flush=True)
            begin = time.perf_counter()
            branch, _ = ml.WepeNerdLoadLoraMasked().load(model, args.adapter.name, strength, mask=mask,
                start_percent=start, end_percent=end, apply_to=mode)
            context = next((value.context for value in branch.object_patches.values() if isinstance(value, ml.LayerDispatcher)), None)
            runs = []
            def record(executor, *pos, **kw):
                runs.append(context.runtime.get())
                return executor(*pos, **kw)
            if context is not None:
                branch.add_wrapper_with_key(pe.WrappersMP.OUTER_SAMPLE, "evidence", record)
            row = dict(name=name, start_percent=start, end_percent=end, apply_to=mode,
                       sigma_start=model.get_model_object("model_sampling").percent_to_sigma(start),
                       sigma_end=model.get_model_object("model_sampling").percent_to_sigma(end), call_times=[])
            def timing(executor, *pos, **kw):
                torch.cuda.synchronize()
                before = time.perf_counter()
                output = executor(*pos, **kw)
                torch.cuda.synchronize()
                row["call_times"].append(time.perf_counter() - before)
                return output
            branch.add_wrapper_with_key(pe.WrappersMP.APPLY_MODEL, "timing", timing)
            torch.cuda.reset_peak_memory_stats()
            with torch.set_grad_enabled(False):
                result = comfy.sample.sample_custom(branch, noise, 3., sampler, sigmas, positive, negative, latent, seed=args.seed)
            torch.cuda.synchronize()
            row.update(sampling_seconds=time.perf_counter()-begin,
                       warm_forward_seconds=sum(row["call_times"][1:]),
                       peak_allocated_bytes=torch.cuda.max_memory_allocated(), peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                       runtime=runs[0].stats if runs else {}, runtime_closed=runs[0].closed if runs else None,
                       latent_sha256=hashlib.sha256(result.numpy().tobytes()).hexdigest())
            torch.save(result, args.output / (name + ".pt"))
            if vae is None:
                vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(str(args.vae)))
            with torch.set_grad_enabled(False):
                decoded = vae.decode(result)
                # Native Wan VAE returns a frame axis even for a single still image.
                pixels = decoded.reshape(-1, *decoded.shape[-3:])[0].cpu().clamp(0, 1).numpy()
            image = Image.fromarray((pixels*255).round().astype(np.uint8))
            image.save(args.output / (name + ".png"))
            images.append((name, image))
            row["total_with_decode_seconds"] = time.perf_counter()-begin
            evidence["runs"].append(row)
            save()
        sheet = Image.new("RGB", (args.width*2, (args.height+32)*2), "#181a20")
        draw = ImageDraw.Draw(sheet)
        for i, (name, image) in enumerate(images):
            x, y = i%2*args.width, i//2*(args.height+32)
            draw.text((x+8, y+8), name, fill="white")
            sheet.paste(image, (x, y+32))
        sheet.save(args.output / "comparison.png")
        evidence["status"] = "completed"
    except Exception as error:
        evidence.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
