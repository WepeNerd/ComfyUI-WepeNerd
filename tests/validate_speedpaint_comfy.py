"""Run against an isolated ComfyUI server with a disposable input/output folder."""
import argparse
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--vae", default="vae-ft-mse-840000-ema-pruned.safetensors")
    args = parser.parse_args()

    def request(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(args.url + path, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    document = request("/wepenerd/speedpaint/prepare", {
        "document": {"v": 1, "background": "#247fa1", "painted": False}, "width": 128, "height": 128,
    })
    for payload in (
        {"document": document, "width": 63, "height": 128},
        {"document": {"v": 1, "source": "../../private.png"}, "width": 128, "height": 128},
        {"document": ["invalid"], "width": 128, "height": 128},
    ):
        try:
            request("/wepenerd/speedpaint/prepare", payload)
        except urllib.error.HTTPError as error:
            assert error.code == 400, (error.code, error.read())
        else:
            raise AssertionError("Invalid preparation was accepted")
    print("PASS bounded route errors", flush=True)
    prompt = {
        "1": {"class_type": "WN_ResolutionSuggest", "inputs": {"width": 160, "height": 96, "target": 128,
              "resize_mode": "Longest Side", "divisor": 16, "snap_mode": "round"}},
        "2": {"class_type": "WN_Speedpaint", "inputs": {"width": ["1", 0], "height": ["1", 1], "document": json.dumps(document)}},
        "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": args.vae}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["4", 0]}},
        "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}},
    }
    queued = request("/prompt", {"prompt": prompt, "client_id": "speedpaint-validation-" + uuid.uuid4().hex})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        history = request("/history/" + queued["prompt_id"])
        if queued["prompt_id"] in history:
            result = history[queued["prompt_id"]]
            assert result["status"]["status_str"] == "success", result["status"]
            for node_id in ("3", "7"):
                descriptor = result["outputs"][node_id]["images"][0]
                with urllib.request.urlopen(args.url + "/view?" + urllib.parse.urlencode(descriptor)) as response:
                    image = Image.open(io.BytesIO(response.read())).convert("RGB")
                assert image.size == (128, 80), image.size
                if node_id == "3":
                    assert image.getpixel((50, 40)) == (36, 127, 161)
            resolved = result["outputs"]["2"]["speedpaint"][0]
            assert (resolved["width"], resolved["height"]) == (128, 80)
            assert resolved["document"] == json.dumps(document)
            print("PASS runtime-computed dimensions and exact RGB IMAGE", flush=True)
            print("PASS real VAE Encode -> VAE Decode with " + args.vae, flush=True)
            return
        time.sleep(.5)
    raise TimeoutError("ComfyUI did not finish the validation prompt")


if __name__ == "__main__":
    main()
