"""Find the objects in a photo, so a user can tap one instead of drawing a box.

An open-vocabulary detector takes a list of object names and returns boxes.
Its labels are unreliable on cluttered phone photos (Grounding DINO called a
JBL speaker a "mobile phone"), but its boxes are good. So the detector only
proposes boxes; the catalog search decides what the object is.

Benchmark on four real phone photos, 2012 dual-core CPU:
  OmDet-Turbo           5-9 s a photo, found every object, sensible labels
  OWL-ViT B/32          ~4 s, one box a photo, missed a speaker
  Grounding DINO tiny   ~31 s, good boxes, muddled labels
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

# What to look for: the catalog's kinds of product, plus everyday things a
# user might tap that we don't stock (those get the "no close match" state).
LABELS = [
    "sofa", "chair", "bed", "table", "television", "laptop", "mobile phone", "smart watch",
    "speaker", "shoe", "handbag", "dress", "shirt", "trousers", "jacket",
    "remote control", "computer mouse", "lamp", "cup", "bottle",
]


@dataclass(frozen=True)
class Detection:
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels
    label: str
    score: float


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    model_id: str  # on the Hugging Face Hub; both are Apache-2.0
    kind: str  # "omdet" or "grounding-dino": they take prompts and return results differently
    threshold: float


DETECTORS = {
    spec.name: spec
    for spec in (
        DetectorSpec("omdet-turbo", "omlab/omdet-turbo-swin-tiny-hf", "omdet", 0.3),
        DetectorSpec("grounding-dino-tiny", "IDEA-Research/grounding-dino-tiny", "grounding-dino", 0.3),
    )
}
DEFAULT_DETECTOR = "omdet-turbo"


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def clean(
    detections: list[Detection],
    width: int,
    height: int,
    *,
    overlap: float = 0.7,
    min_side: int = 24,
    background: float = 0.85,
    limit: int = 12,
) -> list[Detection]:
    """Drop tiny boxes and near-duplicates (the same object under two names), best first.

    A box covering nearly the whole photo is usually the background ("sofa" for
    the couch a photo was taken on), so it is dropped when there are other
    objects. Alone, it is kept: a close-up of a laptop fills the frame.
    """
    kept: list[Detection] = []
    for d in sorted(detections, key=lambda d: d.score, reverse=True):
        x1, y1, x2, y2 = max(0, d.box[0]), max(0, d.box[1]), min(width, d.box[2]), min(height, d.box[3])
        if x2 - x1 < min_side or y2 - y1 < min_side:
            continue
        box = (x1, y1, x2, y2)
        if any(iou(box, k.box) > overlap for k in kept):
            continue
        kept.append(Detection(box, d.label, d.score))
    area = width * height
    objects = [d for d in kept if (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]) < background * area]
    return (objects or kept)[:limit]


def _first_label(text: str) -> str:
    """Grounding DINO can glue several prompt phrases together ("mobile phone smart watch"); keep the first."""
    found = [(text.find(label), label) for label in LABELS if label in text]
    return min(found)[1] if found else text.strip() or "object"


class Detector:
    def __init__(self, spec: DetectorSpec):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self.spec = spec
        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(spec.model_id)
        if spec.kind == "omdet":
            from transformers import OmDetTurboForObjectDetection

            self._model = OmDetTurboForObjectDetection.from_pretrained(spec.model_id).eval()
        else:
            self._model = AutoModelForZeroShotObjectDetection.from_pretrained(spec.model_id).eval()

    def detect(self, image: Image.Image) -> list[Detection]:
        size = [(image.height, image.width)]
        if self.spec.kind == "omdet":
            inputs = self._processor(image, text=LABELS, return_tensors="pt")
            with self._torch.inference_mode():
                outputs = self._model(**inputs)
            result = self._processor.post_process_grounded_object_detection(
                outputs, text_labels=[LABELS], target_sizes=size, threshold=self.spec.threshold, nms_threshold=0.3
            )[0]
        else:
            inputs = self._processor(images=image, text=". ".join(LABELS) + ".", return_tensors="pt")
            with self._torch.inference_mode():
                outputs = self._model(**inputs)
            result = self._processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, threshold=self.spec.threshold, text_threshold=0.25, target_sizes=size
            )[0]
        labels = result.get("text_labels", result.get("labels"))
        found = [
            Detection(tuple(round(float(v)) for v in box), _first_label(str(label)), float(score))
            for box, label, score in zip(result["boxes"], labels, result["scores"])
        ]
        return clean(found, image.width, image.height)
