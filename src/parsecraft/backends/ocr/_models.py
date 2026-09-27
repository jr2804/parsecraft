"""Model inventory for the OCR backends — one source of truth, no heavy imports.

Pinned revisions and licenses verified against the HF API 2026-09-27
(plan model section, external to this repo). Consumed by the light factories
(descriptors) and the heavy impl modules (``from_pretrained`` pins) alike.
"""

from __future__ import annotations

from parsecraft.backends.protocol import BackendCapabilities, ModelAssetDescriptor

#: Shared backend version for the whole OCR family (adapter code, not model).
OCR_BACKEND_VERSION = "0.1.0"

OVIS_NAME = "ocr-ovis"
OVIS_MODEL_ID = "ATH-MaaS/OvisOCR2"
OVIS_REVISION = "1fc9221b7823a371d6e97f92d527cc847e24e107"
OVIS_VRAM_GB = 1.0
OVIS_EXTRA = "ocr-ovis"

TELE_NAME = "ocr-tele"
TELE_MODEL_ID = "StarDoc-AI/TeleOCR"
TELE_REVISION = "a61433186527cb53958bf354e34ae673c19cec4b"
TELE_VRAM_GB = 1.2
TELE_EXTRA = "ocr-tele"

UNLIMITED_NAME = "ocr-unlimited"
UNLIMITED_MODEL_ID = "baidu/Unlimited-OCR"
UNLIMITED_REVISION = "07dea832e22aefee32ad281d4b80551282e1c168"
UNLIMITED_VRAM_GB = 3.0
UNLIMITED_EXTRA = "ocr-unlimited"
#: ``infer_multi()`` batch size: plan default is deliberately small.
UNLIMITED_MAX_PAGES_PER_CALL = 4

QIANFAN_NAME = "ocr-qianfan"
QIANFAN_MODEL_ID = "baidu/Qianfan-OCR"
QIANFAN_REVISION = "623bf5d20d446abdb36606aa4547cd0c18886fe5"
QIANFAN_VRAM_GB = 4.0
QIANFAN_EXTRA = "ocr-qianfan"

#: One MIME vocabulary for eligibility (native backends declare MIME too):
#: `RoutingConstraints.formats` carries the source's media type, so extensions
#: here would exclude every OCR backend from PDF/image sources (found while
#: wiring `convert --auto`). Exactly the payloads `_common.count_pages` /
#: `rasterize_page` accept — tiff/webp stay out until the impls handle them.
OCR_FORMATS: tuple[str, ...] = ("application/pdf", "image/jpeg", "image/png")

OVIS_ASSET = ModelAssetDescriptor(
    model_id=OVIS_MODEL_ID,
    model_revision=OVIS_REVISION,
    model_license="apache-2.0",
    code_license="MIT",
    asset_license="apache-2.0",
    requires_user_acceptance=False,
    source_urls=[
        "https://huggingface.co/ATH-MaaS/OvisOCR2",
        "https://huggingface.co/Qwen/Qwen3.5-0.8B",
        "https://arxiv.org/abs/2607.13639",
    ],
    size_bytes=None,
    quantization=None,
    estimated_vram_gb=OVIS_VRAM_GB,
)

TELE_ASSET = ModelAssetDescriptor(
    model_id=TELE_MODEL_ID,
    model_revision=TELE_REVISION,
    model_license="apache-2.0",
    code_license="MIT",
    asset_license="apache-2.0",
    requires_user_acceptance=False,
    source_urls=[
        "https://huggingface.co/StarDoc-AI/TeleOCR",
        "https://github.com/caipeng328/TeleOCR",
        "https://arxiv.org/abs/2608.12898",
    ],
    size_bytes=None,
    quantization=None,
    estimated_vram_gb=TELE_VRAM_GB,
)

UNLIMITED_ASSET = ModelAssetDescriptor(
    model_id=UNLIMITED_MODEL_ID,
    model_revision=UNLIMITED_REVISION,
    model_license="MIT",
    code_license="MIT",
    asset_license="MIT",
    requires_user_acceptance=False,
    source_urls=[
        "https://huggingface.co/baidu/Unlimited-OCR",
        "https://github.com/baidu/Unlimited-OCR",
        "https://arxiv.org/abs/2606.23050",
    ],
    size_bytes=None,
    quantization=None,
    estimated_vram_gb=UNLIMITED_VRAM_GB,
)

QIANFAN_ASSET = ModelAssetDescriptor(
    model_id=QIANFAN_MODEL_ID,
    model_revision=QIANFAN_REVISION,
    model_license="apache-2.0",
    code_license="MIT",
    asset_license="apache-2.0",
    requires_user_acceptance=False,
    source_urls=[
        "https://huggingface.co/baidu/Qianfan-OCR",
        "https://github.com/baidubce/Qianfan-VL",
        "https://arxiv.org/abs/2603.13398",
    ],
    size_bytes=None,
    quantization=None,
    estimated_vram_gb=QIANFAN_VRAM_GB,
)


def ocr_capabilities(
    *,
    asset: ModelAssetDescriptor,
    optional_dependency_group: str,
    requires_gpu: bool,
    supports_multi_page: bool,
    estimated_vram_gb: float,
) -> BackendCapabilities:
    """Build the capability record shared by a factory descriptor and its backend."""
    return BackendCapabilities(
        supported_formats=list(OCR_FORMATS),
        supports_page_ranges=True,
        supports_multi_page=supports_multi_page,
        requires_gpu=requires_gpu,
        estimated_vram_gb=estimated_vram_gb,
        optional_dependency_group=optional_dependency_group,
        model_asset=asset,
    )


OVIS_CAPABILITIES = ocr_capabilities(
    asset=OVIS_ASSET,
    optional_dependency_group=OVIS_EXTRA,
    requires_gpu=True,
    supports_multi_page=False,
    estimated_vram_gb=OVIS_VRAM_GB,
)

TELE_CAPABILITIES = ocr_capabilities(
    asset=TELE_ASSET,
    optional_dependency_group=TELE_EXTRA,
    requires_gpu=True,
    supports_multi_page=False,
    estimated_vram_gb=TELE_VRAM_GB,
)

UNLIMITED_CAPABILITIES = ocr_capabilities(
    asset=UNLIMITED_ASSET,
    optional_dependency_group=UNLIMITED_EXTRA,
    requires_gpu=True,
    supports_multi_page=True,
    estimated_vram_gb=UNLIMITED_VRAM_GB,
)

QIANFAN_CAPABILITIES = ocr_capabilities(
    asset=QIANFAN_ASSET,
    optional_dependency_group=QIANFAN_EXTRA,
    requires_gpu=True,
    supports_multi_page=False,
    estimated_vram_gb=QIANFAN_VRAM_GB,
)
