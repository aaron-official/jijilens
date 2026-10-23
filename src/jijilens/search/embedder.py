"""Image embedding models.

An embedding model turns an image into a vector so that images of similar
things end up close together. Vectors are L2-normalised, so the dot product of
two of them is their cosine similarity (1 = same direction, 0 = unrelated).

torch and open_clip are imported when a model is loaded, not at import time,
so commands that don't embed anything start fast.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class ModelSpec:
    name: str  # our name, stored in embedding_models
    arch: str  # open_clip architecture
    pretrained: str  # open_clip weights tag
    dim: int
    # Similarity at or above which a result counts as a close match, from `jijilens eval`
    # calibration. None until a model has been calibrated.
    close_match: float | None = None


MODELS = {
    spec.name: spec
    for spec in (
        # Google's SigLIP 2, ViT-B/16 at 224 px: the quality pick. ~1.1 images/s on a 2012 dual-core CPU.
        # Threshold: own-listing similarity p10 is 0.77, best other-category match p50 is 0.77, p75 0.82.
        ModelSpec("siglip2-b16", "ViT-B-16-SigLIP2", "webli", 768, close_match=0.80),
        # Apple's MobileCLIP2-S0, built for phones: ~5x faster on the same CPU, lower quality.
        ModelSpec("mobileclip2-s0", "MobileCLIP2-S0", "dfndr2b", 512),
    )
}
DEFAULT_MODEL = "siglip2-b16"


def get_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError:
        raise ValueError(f"unknown model {name!r}; choose from {', '.join(MODELS)}") from None


class Embedder:
    def __init__(self, spec: ModelSpec):
        import open_clip
        import torch

        self.spec = spec
        self._torch = torch
        model, _, preprocess = open_clip.create_model_and_transforms(spec.arch, pretrained=spec.pretrained)
        self._model = model.eval()
        self._preprocess = preprocess

    def embed(self, images: list[Image.Image]) -> np.ndarray:
        """One L2-normalised float32 row per image."""
        torch = self._torch
        batch = torch.stack([self._preprocess(image.convert("RGB")) for image in images])
        with torch.inference_mode():
            features = self._model.encode_image(batch).float()
        features = torch.nn.functional.normalize(features, dim=-1)
        return features.numpy().astype(np.float32)
