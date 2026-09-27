"""Model inventory for the OCR backends — one source of truth, no heavy imports.

Pinned revisions and licenses verified against the HF API 2026-09-27
(plan model section, external to this repo). Consumed by the light factories
(descriptors) and the heavy impl modules (``from_pretrained`` pins) alike.
"""

from __future__ import annotations

from parsecraft.backends.protocol import AssetFilePin, BackendCapabilities, ModelAssetDescriptor

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

OVIS_FILE_PINS: tuple[AssetFilePin, ...] = (
    AssetFilePin(path="chat_template.jinja", sha256="273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80", size=7755),
    AssetFilePin(path="config.json", sha256="b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204", size=2907),
    AssetFilePin(path="merges.txt", sha256="a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d", size=3353259),
    AssetFilePin(path="model.safetensors", sha256="9270560288656ece5cb3a6989001afcf5af8d223bceed4a423c33a008861d009", size=1706030496),
    AssetFilePin(path="preprocessor_config.json", sha256="27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516", size=390),
    AssetFilePin(path="tokenizer.json", sha256="5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42", size=12807982),
    AssetFilePin(path="tokenizer_config.json", sha256="49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c", size=16709),
    AssetFilePin(path="video_preprocessor_config.json", sha256="7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13", size=385),
    AssetFilePin(path="vocab.json", sha256="ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003", size=6722759),
)

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
    file_pins=OVIS_FILE_PINS,
)

TELE_FILE_PINS: tuple[AssetFilePin, ...] = (
    AssetFilePin(path="added_tokens.json", sha256="58b54bbe36fc752f79a24a271ef66a0a0830054b4dfad94bde757d851968060b", size=605),
    AssetFilePin(path="chat_template.jinja", sha256="a0bc6f6fc7a29a80017a433e8f03a1cc1236e838a944a2d034295a60c4f2fddb", size=1017),
    AssetFilePin(path="config.json", sha256="92c5e15f1768dcb54bceae170043fbfefd248508159700b2179824b88c33ae75", size=3516),
    AssetFilePin(path="generation_config.json", sha256="50867494bddf9ddc8e289e6214be5a2263e68f16c127a3652c6eb9f17990e5d9", size=244),
    AssetFilePin(path="merges.txt", sha256="8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5", size=1671853),
    AssetFilePin(path="model.safetensors", sha256="9817b18041bd96403f75e38a33b28ed0cd5fb2641f67eead673afabd3c408109", size=2830223488),
    AssetFilePin(path="modeling_naviocr.py", sha256="7adac1cc17009f9f1b8e0c58284d7c0b9989a590f017b9e5964cc42270207922", size=84057),
    AssetFilePin(path="preprocessor_config.json", sha256="f2058c716eef96ccaed1cc1e2d0c08306b62586d535b28d9d08e691b2fab7ca0", size=350),
    AssetFilePin(path="special_tokens_map.json", sha256="76862e765266b85aa9459767e33cbaf13970f327a0e88d1c65846c2ddd3a1ecd", size=613),
    AssetFilePin(path="tokenizer.json", sha256="9c5ae00e602b8860cbd784ba82a8aa14e8feecec692e7076590d014d7b7fdafa", size=11421896),
    AssetFilePin(path="tokenizer_config.json", sha256="e04be4dd17e9f661bc487ba848a37f66a8f1adf2eef412f5d4ecb40cab4d39e7", size=4730),
    AssetFilePin(path="video_preprocessor_config.json", sha256="15bb7c2f2bc95fe9cc3749a4b287872b4886a15e9fcf550a4122a46ae26150bd", size=913),
    AssetFilePin(path="vocab.json", sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910", size=2776833),
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
    file_pins=TELE_FILE_PINS,
)

UNLIMITED_FILE_PINS: tuple[AssetFilePin, ...] = (
    AssetFilePin(path="config.json", sha256="27246d03fd670904ec9601b1cb0861fbb79ec076830771daa8d943d6229946f9", size=2881),
    AssetFilePin(path="configuration_deepseek_v2.py", sha256="b8470dd616ba8745fce6e27b093aef73a098863cc891b2477dcf9326a36000f7", size=10720),
    AssetFilePin(path="conversation.py", sha256="ec7b6ce89bcda643de1f43269ffa66a7b2e65dc3ed30e427958f776546b4ba03", size=9253),
    AssetFilePin(path="deepencoder.py", sha256="0ae2fb6d1e5ae8cf100fc32f854830acd08c821a0a1f23a94a76588c222ddcf2", size=38008),
    AssetFilePin(path="model-00001-of-000001.safetensors", sha256="2bc48a7a110061ea58fff65d3169367eebe3aee371ca6968dc2219c1b2855fc6", size=6672547120),
    AssetFilePin(path="model.safetensors.index.json", sha256="354be1f2dcfb72ebb385e25465522ce5413a77c36f3b35fec088a3162a11af99", size=257611),
    AssetFilePin(path="modeling_deepseekv2.py", sha256="74e36e6bd0ba7bc565ef76464a99baa8e6bccb710ae9c1007b54ac30b855fa4c", size=90162),
    AssetFilePin(path="modeling_unlimitedocr.py", sha256="268bdcbe12cf37bf5a2debb53faf542e56570958a5d9f3314aab3cab2cf6cb48", size=53431),
    AssetFilePin(path="processor_config.json", sha256="92588cffb1d7032ec83d0a06c3a5171b41df5cbf432d68765441139a57899328", size=466),
    AssetFilePin(path="special_tokens_map.json", sha256="ab4bd57ce17d62e39e0a39e739de1e407484f090f0b2c7e391312bca7a5b061a", size=801),
    AssetFilePin(path="tokenizer.json", sha256="a02f8fd5228c90256bb4f6554c34a579d48f909e5beb232dc4afad870b55a8b4", size=9979544),
    AssetFilePin(path="tokenizer_config.json", sha256="a0cbe8464049da1f891b7a12676de06af4cb54c130995d42f71adc1c30c6e9f3", size=165938),
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
    file_pins=UNLIMITED_FILE_PINS,
)

QIANFAN_FILE_PINS: tuple[AssetFilePin, ...] = (
    AssetFilePin(path="added_tokens.json", sha256="df52ebcc0d08ca9d9110a6e323b7a6ed4478aff1a4c23b08fa2823737731bf0c", size=26115),
    AssetFilePin(path="chat_template.jinja", sha256="645f5920e77aaad66fa68e2744728ef3f75f982265c399cf8439a48ac776e2be", size=3592),
    AssetFilePin(path="config.json", sha256="038026e7011e3a575c29f8c5c2cbaa151ab329a2c59461c8f1b51bd5f43a4a1b", size=1983),
    AssetFilePin(path="generation_config.json", sha256="b17e2c6813e1f5e4de267a4c7697b46f69ba7ba95643c8253ad671fab7d7271c", size=121),
    AssetFilePin(path="merges.txt", sha256="8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5", size=1671853),
    AssetFilePin(path="model-00001-of-00002.safetensors", sha256="e2a59915dd6a1c51ccb11be3addf4585fcf0840ac4f63f8e9fb629db58f8db6e", size=4979120456),
    AssetFilePin(path="model-00002-of-00002.safetensors", sha256="96805d61fbb9523fd27a09ab40451d04da09e9ba4b102341eac0184d8f82a0b1", size=4503788440),
    AssetFilePin(path="model.safetensors.index.json", sha256="8ac669bb58b00ff50fcae251c0002e0a2aaefbe7f1e90523d9af1c21807f0242", size=68445),
    AssetFilePin(path="preprocessor_config.json", sha256="4b47e61791af154ba0f46b332c97a3077b44e30443b7a2297394da012a52fb7e", size=662),
    AssetFilePin(path="processor_config.json", sha256="d5665e6cc31622020ed730196f9b7ddca6ab047f6e3501ea7ac8cd760ccae971", size=179),
    AssetFilePin(path="special_tokens_map.json", sha256="98e23555b0863690619b0414d458b4bc5f8c2f431c3492e22a6d2f15dcd46053", size=1314),
    AssetFilePin(path="tokenizer.json", sha256="29d2b7ab0e0ecc235df0d7d87fc3ac9b90826605e42117898c653e436c97fd03", size=11615171),
    AssetFilePin(path="tokenizer_config.json", sha256="65b1f6f2889a2485db34117e167a21e06f4a0b80fb7c1d4f59342a794c95876a", size=187656),
    AssetFilePin(path="video_preprocessor_config.json", sha256="b7f9c784a27f30ddb3fc78fca353ab6ce982c2a263b702e2547302e8c1e0087a", size=1345),
    AssetFilePin(path="vocab.json", sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910", size=2776833),
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
    file_pins=QIANFAN_FILE_PINS,
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
