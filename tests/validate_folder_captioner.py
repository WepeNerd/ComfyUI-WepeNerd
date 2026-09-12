"""Optional real-model smoke check; copies a fixture into a disposable folder."""

import argparse
from dataclasses import asdict
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
package = types.ModuleType("wepenerd_caption_validation")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
nodes = importlib.import_module(package.__name__ + ".wn_gguf_nodes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--projector", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--gpu-layers", type=int, default=8)
    parser.add_argument("--skills", nargs="+", choices=["character", "style", "refiner"],
                        default=["character", "style", "refiner"])
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    config = nodes.WNGGUFConfig(
        model_path=args.model, mmproj_path=args.projector, gpu_layers=args.gpu_layers,
        target_free_vram_mb=0, comfy_vram_handoff="never", release_after_generate=False,
        context_size=4096, image_max_tokens=512, request_timeout_s=300,
    )
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="wn-folder-caption-smoke-") as directory:
            for index, (skill, trigger, context) in enumerate([
                ("Krea 2 - Character likeness", "WNperson", "The main person is the target character. Learn identity; clothing should remain changeable."),
                ("Krea 2 - Style", "WNstyle", "Learn the image's photographic treatment, separate from its subject."),
                ("Krea 2 - Refiner", "", "Refine the visible structure and placement of eyeglasses. Describe the glasses and their relationship to the face."),
            ]):
                if ["character", "style", "refiner"][index] not in args.skills:
                    continue
                dataset = Path(directory) / str(index)
                dataset.mkdir()
                shutil.copyfile(args.image, dataset / ("example" + Path(args.image).suffix))
                if index == 0:
                    shutil.copyfile(args.image, dataset / ("second" + Path(args.image).suffix))
                start = time.monotonic()
                print(f"START {skill}", flush=True)
                result = nodes.WN_FolderCaptioner().caption(
                    config, str(dataset), skill, trigger, context,
                    "Use at most three short sentences.", max_tokens=384, image_max_edge=512,
                )
                expected = 2 if index == 0 else 1
                assert result[1:] == (expected, 0), result
                captions = [path.read_text(encoding="utf-8").strip() for path in sorted(dataset.glob("*.txt"))]
                assert len(captions) == expected and all(captions)
                if trigger:
                    assert all(trigger in caption for caption in captions)
                repeat = nodes.WN_FolderCaptioner().caption(config, str(dataset), skill)
                assert repeat[1:] == (0, expected), repeat
                entry = {"skill": skill, "seconds": round(time.monotonic() - start, 2),
                         "written": expected, "resume_skipped": repeat[2], "captions": captions}
                results.append(entry)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
    finally:
        nodes.SERVER_MANAGER.stop()
        Path(args.report).write_text(json.dumps({"config": asdict(config), "fixture": args.image,
                                                "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
