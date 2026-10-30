from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from .store import InputError, atomic_text


MODELS = {
    "segmentation": {
        "filename": "segmentation.onnx",
        "sha256": "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "member": "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
        "original_model": "https://huggingface.co/pyannote/segmentation-3.0",
        "attribution": "pyannote segmentation-3.0, pyannote authors; ONNX conversion distributed by k2-fsa/sherpa-onnx",
        "license": "MIT",
        "license_url": "https://huggingface.co/pyannote/segmentation-3.0/blob/main/LICENSE",
    },
    "eres2net": {
        "filename": "eres2net.onnx",
        "sha256": "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        "original_model": "https://modelscope.cn/models/iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k",
        "attribution": "3D-Speaker ERes2Net-Base, Alibaba DAMO Academy; ONNX conversion distributed by k2-fsa/sherpa-onnx",
        "license": "Apache-2.0",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0",
        "training_domain": "Chinese 3D-Speaker speech; cross-language performance is not guaranteed",
    },
    "titanet": {
        "filename": "titanet.onnx",
        "sha256": "d51abcf31717ef28162f26acb9d44dd4127c3d44c9b8624f699f3425daca8e77",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_large.onnx",
        "original_model": "https://huggingface.co/nvidia/speakerverification_en_titanet_large",
        "attribution": "NVIDIA TitaNet-Large, NVIDIA NeMo authors; ONNX conversion distributed by k2-fsa/sherpa-onnx",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
        "training_domain": "English speech; cross-language performance is not guaranteed",
    },
}


def default_models_dir() -> Path:
    if sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "public-appearance" / "models"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verified_models(models_dir: Path | None = None) -> dict[str, Path]:
    folder = Path(models_dir).expanduser() if models_dir is not None else default_models_dir()
    paths = {}
    for key, spec in MODELS.items():
        path = folder / spec["filename"]
        if path.is_symlink() or not path.is_file():
            raise InputError(f"Missing model {path}. Run appearance setup-models with the same --models-dir.")
        if sha256(path) != spec["sha256"]:
            raise InputError(f"Model checksum mismatch: {path}. Run appearance setup-models to replace it.")
        paths[key] = path.resolve()
    return paths


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "public-appearance/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("xb") as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    raise InputError("Model download exceeds the 1 GiB safety limit.")
                output.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise InputError(f"Model download failed from {url}: {type(exc).__name__}. Check connectivity and retry setup-models.") from exc


def setup_models(models_dir: Path | None = None) -> dict:
    folder = Path(models_dir).expanduser() if models_dir is not None else default_models_dir()
    if folder.is_symlink():
        raise InputError("The model directory must not be a symbolic link.")
    folder.mkdir(parents=True, exist_ok=True)
    folder = folder.resolve()
    manifest = {
        "models_dir": str(folder),
        "verification": "SHA256 content pins of official sherpa-onnx release assets, not upstream signatures",
        "license_notice": "Models retain their original licenses, not this plugin's license. Review each original model's terms before use or redistribution.",
        "models": {},
    }
    for key, spec in MODELS.items():
        target = folder / spec["filename"]
        if target.is_symlink():
            raise InputError(f"Refusing symbolic-link model target: {target}")
        cached = target.is_file() and sha256(target) == spec["sha256"]
        if not cached:
            print(f"Downloading {key} model", file=sys.stderr, flush=True)
            with tempfile.TemporaryDirectory(prefix=".model-", dir=folder) as temporary:
                download = Path(temporary) / "download"
                _download(spec["url"], download)
                candidate = download
                if "member" in spec:
                    candidate = Path(temporary) / "model.onnx"
                    expected = PurePosixPath(spec["member"])
                    if expected.is_absolute() or ".." in expected.parts:
                        raise InputError("Invalid pinned model archive member.")
                    try:
                        with tarfile.open(download, "r:bz2") as archive:
                            matches = [entry for entry in archive if entry.name == str(expected)]
                            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 128 * 1024 * 1024:
                                raise InputError("Model archive does not contain one regular expected model member.")
                            # Never let archive paths or links reach the filesystem.
                            member = archive.extractfile(matches[0])
                            if member is None:
                                raise InputError("Cannot read expected model archive member.")
                            with member, candidate.open("xb") as output:
                                shutil.copyfileobj(member, output)
                    except (tarfile.TarError, OSError) as exc:
                        raise InputError(f"Cannot extract segmentation model: {exc}") from exc
                digest = sha256(candidate)
                if digest != spec["sha256"]:
                    raise InputError(f"SHA256 mismatch for {key}; refusing downloaded model. Expected {spec['sha256']}, received {digest}.")
                if target.is_symlink():
                    raise InputError(f"Refusing symbolic-link model target: {target}")
                candidate.replace(target)
        manifest["models"][key] = {**spec, "path": str(target), "verified": True, "cached": cached}
    atomic_text(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return manifest
