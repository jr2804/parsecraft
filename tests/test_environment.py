"""Environment probe: detected facts without hardware or network assumptions."""

from __future__ import annotations

import tomllib
from pathlib import Path
from subprocess import TimeoutExpired

import pytest
from pydantic import ValidationError

from parsecraft.backends import registry as registry_module
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)
from parsecraft.environment import probe as probe_module
from parsecraft.environment.constraints import constraints_from_environment
from parsecraft.environment.models import EnvironmentInfo
from parsecraft.environment.probe import EXTRA_IMPORTS, META_EXTRAS, probe_environment
from parsecraft.routing.models import RoutingConstraints, RoutingPreference

#: The one module the probe looks up outside the extras map (CUDA runtime check).
_TORCH = "torch"


class _Completed:
    """Stand-in for ``subprocess.CompletedProcess`` (probe only reads these)."""

    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _FakeFactory:
    """Entry-point factory stand-in: descriptor only — the probe never instantiates."""

    def __init__(self, descriptor: BackendDescriptor) -> None:
        self.descriptor = descriptor

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        raise AssertionError(f"the probe must never build backends: {config.name}")


class _EntryPoint:
    def __init__(self, factory: _FakeFactory) -> None:
        self.name = factory.descriptor.name
        self._factory = factory

    def load(self) -> _FakeFactory:
        return self._factory


class _Spec:
    """Stand-in for ``importlib.machinery.ModuleSpec`` (the probe reads ``origin``)."""

    def __init__(self, origin: str | None) -> None:
        self.origin = origin


# ── GPU detection ───────────────────────────────────────────────────────────────


def test_probe_reports_zero_budget_without_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_smi(monkeypatch, error=FileNotFoundError("nvidia-smi"))
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().vram_budget_gb == 0.0


def test_probe_reads_vram_from_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_smi(monkeypatch, stdout="8192 MiB\n")
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().vram_budget_gb == 8.0


def test_probe_queries_exactly_the_documented_nvidia_smi_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> _Completed:
        captured["args"] = args[0] if args else None
        captured["timeout"] = kwargs.get("timeout")
        return _Completed(stdout="512 MiB\n")

    monkeypatch.setattr(probe_module, "run", _fake_run)
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().vram_budget_gb == 0.5
    assert captured["args"] == ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"]
    assert captured["timeout"] == 10.0


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        ("garbage, no unit\n", 0),
        ("", 0),
        ("N/A\n", 1),
    ],
)
def test_probe_tolerates_malformed_nvidia_smi_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
) -> None:
    _patch_smi(monkeypatch, stdout=stdout, returncode=returncode)
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().vram_budget_gb == 0.0


def test_probe_tolerates_smi_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_smi(monkeypatch, error=TimeoutExpired(cmd="nvidia-smi", timeout=10.0))
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().vram_budget_gb == 0.0


def test_cuda_runtime_note_reports_a_missing_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_module, "find_spec", lambda name: None)
    note = probe_module.cuda_runtime_note()
    assert note is not None
    assert "is not installed" in note


def test_cuda_runtime_note_reports_a_spec_without_an_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_module, "find_spec", lambda name: _Spec(None))
    note = probe_module.cuda_runtime_note()
    assert note is not None
    assert "cannot determine the installed torch" in note


def test_cuda_runtime_note_is_none_for_a_cuda_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_torch(monkeypatch, tmp_path, "__version__ = '2.14.0'\ncuda: Optional[str] = '12.6'\n")
    assert probe_module.cuda_runtime_note() is None


@pytest.mark.parametrize("value", ["None", "''", '""'])
def test_cuda_runtime_note_explains_a_cpu_only_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    _install_fake_torch(monkeypatch, tmp_path, f"__version__ = '2.13.0+cpu'\ncuda: Optional[str] = {value}\n")
    note = probe_module.cuda_runtime_note()
    assert note is not None
    assert "has no CUDA support" in note


def test_cuda_runtime_note_reports_an_unreadable_version_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_torch(monkeypatch, tmp_path, version_py=None)
    note = probe_module.cuda_runtime_note()
    assert note is not None
    assert "cannot read" in note


def test_cuda_runtime_note_reports_a_build_that_declares_no_cuda_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_torch(monkeypatch, tmp_path, "__version__ = '2.14.0'\n")
    note = probe_module.cuda_runtime_note()
    assert note is not None
    assert "does not declare a CUDA version" in note


def test_probe_marks_the_gpu_unusable_when_torch_is_a_cpu_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_smi(monkeypatch, stdout="8192 MiB\n")
    _patch_entry_points(monkeypatch, [])
    _install_fake_torch(monkeypatch, tmp_path, "cuda: Optional[str] = None\n")
    environment = probe_environment()
    assert environment.vram_budget_gb == 8.0  # hardware fact stays visible
    assert environment.gpu_usable is False


def test_probe_marks_the_gpu_usable_for_a_cuda_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_smi(monkeypatch, stdout="8192 MiB\n")
    _patch_entry_points(monkeypatch, [])
    _install_fake_torch(monkeypatch, tmp_path, "cuda = '12.6'\n")
    environment = probe_environment()
    assert (environment.vram_budget_gb, environment.gpu_usable) == (8.0, True)


def test_probe_is_not_usable_without_a_visible_gpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_smi(monkeypatch, error=FileNotFoundError("nvidia-smi"))
    _patch_entry_points(monkeypatch, [])
    _install_fake_torch(monkeypatch, tmp_path, "cuda = '12.6'\n")
    environment = probe_environment()
    assert (environment.vram_budget_gb, environment.gpu_usable) == (0.0, False)


# ── CUDA runtime usability (hardware present vs runtime able) ───────────────


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version_py: str | None) -> str:
    """Put a ``torch`` package with the given ``version.py`` where find_spec looks."""
    package = tmp_path / _TORCH
    package.mkdir(exist_ok=True)
    origin = package / "__init__.py"
    origin.write_text("", encoding="utf-8")
    if version_py is not None:
        (package / "version.py").write_text(version_py, encoding="utf-8")
    monkeypatch.setattr(probe_module, "find_spec", lambda name: _Spec(str(origin)) if name == _TORCH else None)
    return str(origin)


# ── Backends and extras ─────────────────────────────────────────────────────────


def test_probe_lists_backends_sorted_from_the_entry_point_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _patch_entry_points(
        monkeypatch,
        [_descriptor("ocr-zeta"), _descriptor("native-alpha")],
    )
    environment = probe_environment()
    assert environment.backends == ("native-alpha", "ocr-zeta")
    assert groups == [registry_module.ENTRY_POINT_GROUP]


@pytest.mark.parametrize(
    ("group", "available_modules", "expected_present"),
    [
        ("ocr-ovis", {"transformers"}, True),
        ("ocr-ovis", set(), False),
        ("pdf", {"pymupdf"}, True),
        ("pdf", set(), False),
    ],
)
def test_probe_detects_extras_through_their_import_packages(
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    available_modules: set[str],
    expected_present: bool,
) -> None:
    _patch_entry_points(monkeypatch, [_descriptor("some-backend", group)])
    _patch_find_spec(monkeypatch, available_modules)
    environment = probe_environment()
    assert (group in environment.installed_extras) is expected_present


def test_probe_never_calls_find_spec_for_unknown_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [_descriptor("future-backend", "brand-new-extra")])
    looked_up = _patch_find_spec(monkeypatch, {"anything"})
    environment = probe_environment()
    assert environment.installed_extras == frozenset()
    # The only lookup is the CUDA-runtime probe (torch), never the unknown extra:
    assert looked_up == [_TORCH]


def test_probe_skips_descriptors_without_a_dependency_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [_descriptor("native-text")])
    looked_up = _patch_find_spec(monkeypatch, set())
    environment = probe_environment()
    assert environment.backends == ("native-text",)
    assert environment.installed_extras == frozenset()
    assert looked_up == [_TORCH]  # only the CUDA-runtime probe


def test_extra_imports_map_covers_the_declared_ocr_and_pdf_groups() -> None:
    for group in ("liteparse", "ocr-ovis", "ocr-tele", "ocr-unlimited", "ocr-qianfan", "pdf", "pdf-inspector", "pdf-lite"):
        assert group in EXTRA_IMPORTS
        assert EXTRA_IMPORTS[group]


def test_every_declared_extra_is_detectable_or_explicitly_meta() -> None:
    """The extras contract, in both directions and against pyproject itself.

    A declared extra in neither map is undetectable — the planner would never
    see it installed — and a stale ``META_EXTRAS`` name would hide that.
    """
    declared = set(_declared_extras())
    assert declared, "pyproject declares no extras — the reader is wrong"
    assert declared == set(EXTRA_IMPORTS) | META_EXTRAS
    assert not set(EXTRA_IMPORTS) & META_EXTRAS  # a meta extra imports nothing
    assert all(EXTRA_IMPORTS[group] for group in EXTRA_IMPORTS)  # every map entry is useful


def _declared_extras() -> tuple[str, ...]:
    """Extra names from pyproject.toml (the single source of truth)."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return tuple(data["project"]["optional-dependencies"])


# ── Offline flag and determinism ────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes "])
def test_offline_flag_is_operator_declared(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PARSECRAFT_OFFLINE", value)
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().offline is True


@pytest.mark.parametrize("value", ["", "0", "no", "maybe"])
def test_offline_defaults_to_false(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PARSECRAFT_OFFLINE", value)
    _patch_entry_points(monkeypatch, [])
    assert probe_environment().offline is False


def test_probe_is_deterministic_for_identical_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [_descriptor("ocr-ovis", "ocr-ovis")])
    _patch_find_spec(monkeypatch, {"transformers"})
    _patch_smi(monkeypatch, stdout="8192 MiB\n")
    monkeypatch.setenv("PARSECRAFT_OFFLINE", "1")
    assert probe_environment() == probe_environment()


def _descriptor(name: str, group: str | None = None) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(optional_dependency_group=group),
    )


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, descriptors: list[BackendDescriptor]) -> list[str]:
    """Install fake entry points; returns every group the registry asked for."""
    points = [_EntryPoint(_FakeFactory(descriptor)) for descriptor in descriptors]
    requested: list[str] = []

    def _fake_entry_points(*, group: str) -> list[_EntryPoint]:
        requested.append(group)
        return points

    monkeypatch.setattr(registry_module, "entry_points", _fake_entry_points)
    return requested


def _patch_find_spec(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> list[str]:
    """Fake ``find_spec``; returns the module names that were looked up."""
    looked_up: list[str] = []

    def _fake_find_spec(name: str) -> object:
        looked_up.append(name)
        return object() if name in present else None

    monkeypatch.setattr(probe_module, "find_spec", _fake_find_spec)
    return looked_up


def _patch_smi(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    returncode: int = 0,
    error: Exception | None = None,
) -> None:
    def _fake_run(*_args: object, **_kwargs: object) -> _Completed:
        if error is not None:
            raise error
        return _Completed(stdout=stdout, returncode=returncode)

    monkeypatch.setattr(probe_module, "run", _fake_run)


def test_environment_info_is_frozen() -> None:
    environment = EnvironmentInfo()
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        environment.vram_budget_gb = 4.0  # ty: ignore[invalid-assignment]


def test_constraints_fill_every_field_from_detected_facts() -> None:
    constraints = constraints_from_environment(_environment())
    assert isinstance(constraints, RoutingConstraints)
    assert constraints.installed_extras == {"ocr-ovis", "pdf-lite"}
    assert constraints.vram_budget_gb == 8.0
    assert constraints.offline is True
    assert constraints.allow_ocr is True  # derived: an OCR extra is detected
    assert constraints.formats == set()
    assert constraints.max_passes == 1


def test_constraints_pass_plan_inputs_through() -> None:
    constraints = constraints_from_environment(
        _environment(),
        formats=("pdf", "md"),
        allow_ocr=False,
        max_passes=3,
        preference=RoutingPreference.QUALITY,
    )
    assert constraints.formats == {"pdf", "md"}
    assert constraints.allow_ocr is False
    assert constraints.max_passes == 3
    assert constraints.preference is RoutingPreference.QUALITY


def test_constraints_default_to_the_balanced_preference() -> None:
    assert constraints_from_environment(_environment()).preference is RoutingPreference.BALANCED


def test_allow_ocr_derivation_and_override() -> None:
    no_ocr = _environment(installed_extras=frozenset({"pdf-lite"}))
    assert constraints_from_environment(no_ocr).allow_ocr is False
    assert constraints_from_environment(no_ocr, allow_ocr=True).allow_ocr is True
    ocr_present = _environment(installed_extras=frozenset({"ocr-tele"}))
    assert constraints_from_environment(ocr_present, allow_ocr=False).allow_ocr is False


def test_constraints_reject_invalid_plan_inputs() -> None:
    with pytest.raises(ValidationError):
        constraints_from_environment(_environment(), max_passes=0)


# ── Bridge into RoutingConstraints ──────────────────────────────────────────────


def _environment(**overrides: object) -> EnvironmentInfo:
    base: dict[str, object] = {
        "backends": ("ocr-ovis",),
        "installed_extras": frozenset({"ocr-ovis", "pdf-lite"}),
        "vram_budget_gb": 8.0,
        "offline": True,
    }
    base.update(overrides)
    return EnvironmentInfo.model_validate(base)
