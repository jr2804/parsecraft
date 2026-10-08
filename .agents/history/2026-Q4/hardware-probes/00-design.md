# Hardware probes — RAM + disk-speed recon/design (pc-925)

- **Status:** design only, no code, no tracked changes. Decision owner: pc-1.
- **Scope:** answer the three bead questions honestly; stdlib-only (no psutil);
  no speculative features (docs/AGENTS.md).

**Verdicts (summary):**

| Question | Verdict | Trigger / condition |
| --- | --- | --- |
| 1. RAM probe | **DEFER** | a registered backend declares a RAM floor that a rule actually reads |
| 2. Disk-speed probe | **DROP** (as probe) | if ever: opt-in diagnostics command only, never `probe_environment()`/startup/`plan_route` |
| 3. Data flow shape | **keep Planned / defer** | no consumer today → no `EnvironmentInfo`/`RoutingConstraints` field, no diagnostic |

## 0. Ground truth: what consumes what TODAY

Verified by grep over `src/parsecraft/` (2026-10-01, HEAD bcc48d9):

| Candidate signal | Consumer today | Evidence |
| --- | --- | --- |
| `vram_budget_gb` | `is_hard_eligible` — GPU backends need `estimated_vram_gb <= budget` | `routing/rules.py:95` |
| `installed_extras` | `is_hard_eligible`, `choose_analyzer` | `routing/rules.py`, `pipeline/analysis.py` |
| `offline` | excludes `model_asset` carriers | `routing/rules.py` |
| disk **capacity** | `models install` pre-download check → `InsufficientDiskSpaceError` | `assets/manager.py:67-69, 202-209` (`shutil.disk_usage(...).free`) |
| disk **capacity** | cache size sweep | `cache/store.py:117` (`sweep(max_bytes)`) |
| disk **speed** | **nothing** | no throughput/latency consumer anywhere |
| **RAM** | **nothing** | no `estimated_ram`/`ram_gb`/`MemTotal`/`RAM` hit outside `*_VRAM_*` constants |

Supporting facts:

- `EnvironmentInfo` carries exactly `backends`, `installed_extras`,
  `vram_budget_gb`, `offline` (`environment/models.py`).
- `RoutingConstraints` carries `formats`, `installed_extras`,
  `vram_budget_gb`, `max_passes`, `allow_ocr`, `offline`, `language`
  (`routing/models.py`). No RAM field.
- `BackendCapabilities` has `requires_gpu` + `estimated_vram_gb` but **no RAM
  field** (`backends/protocol.py:45-69`).
- All four OCR descriptors are `requires_gpu=True`
  (`backends/ocr/_models.py` `OVIS/TELE/UNLIMITED/QIANFAN_CAPABILITIES`); there
  is no CPU OCR path whose RAM need could be declared.

## 1. Q1 — RAM: stdlib API references (design, ready to implement)

Detection cost for all three platforms is microseconds (file read / one
syscall), so cost is **not** the blocker — the absent consumer is.

### Windows — `GlobalMemoryStatusEx` via `ctypes` (stdlib)

```python
from ctypes import wintypes  # Windows-only import, guard with sys.platform

class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [  # MEMORYSTATUSEX: 64 bytes (2×DWORD + 7×DWORDLONG)
        ("dwLength", wintypes.DWORD),            # must be set to sizeof(self
        ("dwMemoryLoad", wintypes.DWORD),        #       before the call)
        ("ullTotalPhys", ctypes.c_uint64),       # ← total physical RAM (bytes)
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
status = _MemoryStatusEx()
status.dwLength = ctypes.sizeof(status)   # 0 / wrong size → ERROR_INVALID_PARAMETER
if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
    raise OSError(ctypes.get_last_error())
total_ram = status.ullTotalPhys
```

Win32 reference: `GlobalMemoryStatusEx` (sysinfoapi.h); structure
`MEMORYSTATUSEX`. No third-party dependency.

### Linux — `/proc/meminfo` (read-only procfs, stdlib `open`)

```python
# lines:  "MemTotal:       16332128 kB"   /   "MemAvailable:    8123456 kB"
total_ram_kb = int(_meminfo_value("MemTotal")) * 1024
avail_kb = int(_meminfo_value("MemAvailable")) * 1024
```

- `MemAvailable` exists since kernel 3.14 (2013); for older kernels the honest
  fallback is `MemFree + Buffers + Cached` (approximation — document it if
  implemented).
- Parse as a pure function over the file text so it is unit-testable offline,
  mirroring the existing `_parse_vram_gb(stdout)` pattern in `probe.py`.

### macOS — `sysctlbyname("hw.memsize")` via `ctypes` (preferred) / `sysctl` binary

```python
libc = ctypes.CDLL(None)  # Darwin: the process' own C library
libc.sysctlbyname.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
    ctypes.c_void_p, ctypes.c_size_t,
]
libc.sysctlbyname.restype = ctypes.c_int
size = ctypes.c_size_t(ctypes.sizeof(ctypes.c_uint64))
buf = ctypes.c_uint64()
rc = libc.sysctlbyname(b"hw.memsize", ctypes.byref(buf), ctypes.byref(size), None, 0)
total_ram = buf.value if rc == 0 else None
```

Exact key: `hw.memsize` (bytes). Subprocess fallback (spawns a process per
probe — slower, less desirable): `sysctl -n hw.memsize`.

### What would RAM gate in routing today? — honest answer: nothing

Consumer candidates examined:

1. **Hard eligibility** — nothing to gate: no descriptor declares a RAM floor
   (`BackendCapabilities` has no field; grep empty). Building the field plus
   the rule would invent a requirement no backend states. **Speculative.**
2. **VRAM-adjacent budgeting when no GPU is present** — not real today: all
   four OCR backends are `requires_gpu=True`, so a `vram_budget_gb == 0.0`
   host excludes them regardless of RAM; letting RAM stand in for VRAM would
   be a *CPU-OCR behavior change* (capability declarations + likely an ADR),
   not something a probe can enable. **Speculative.**
3. **Display-only** (show RAM in `inspect --json`) — cosmetic; a display with
   no rule behind it does not justify an `EnvironmentInfo` field under the
   no-speculative-features rule. **Speculative.**

**Q1 verdict: DEFER.** Keep the design above; implement the probe only when a
registered backend (in-tree or entry-point) declares a RAM floor that
`is_hard_eligible` (or a planning rule) actually reads.

## 2. Q2 — Disk speed: a benchmark is a side effect, not a probe

A synthetic write/read probe would: write megabytes of data (SSD wear, cache
pollution), perturb the very I/O it measures, interfere with antivirus on
Windows, and — because reads are served from the page cache unless one has
root `drop_caches` — return **wrong** cold-start numbers. It also violates the
`environment/AGENTS.md` contract: the probe *detects*, never *estimates
backend performance*, and it must stay bounded — `probe_environment()` runs on
**every** `convert` and `inspect` invocation (`cli/convert.py`,
`cli/inspect.py`), so startup latency would grow for every command.

Placement options:

| Option | Verdict |
| --- | --- |
| Inside `probe_environment()` (runs at CLI startup) | **REJECT** — per-invocation side effect, latency, wear, misleading numbers |
| Opt-in flag on `convert`/`inspect` | **REJECT** — a one-shot conversion cannot consume a throughput figure; nothing would read it |
| Opt-in diagnostics subcommand (e.g. `parsecraft benchmark`-style `--disk`) | only acceptable home **if** a consumer ever appears — measurement belongs where measurement lives |
| Never build it | **RECOMMENDED today** |

Related but distinct: disk **capacity** is already consumed where it matters
(`assets/manager.py` `shutil.disk_usage` before model downloads;
`cache/store.py` size sweep). Speed has no counterpart.

**Q2 verdict: DROP** as a routing/probe input. If a throughput consumer ever
appears, revisit as an explicitly opt-in diagnostics command — never a probe,
never in `plan_route` (planner purity, ADR-0004 decision 1).

## 3. Q3 — Data flow shape: no consumer today → no shape

Honest answer: **neither candidate has a consumer.**

- If RAM ever lands (Q1 trigger), the shape is fixed by parity with VRAM:
  detect in `EnvironmentInfo` (name `ram_budget_gb`, frozen), bridge in
  `constraints_from_environment()` → `RoutingConstraints` → consumed by exactly
  one rule. Detection and consumption ship in the **same** change so no
  dead field ever exists in the frozen public models.
- A diagnostics-only shape (`inspect --json` line / `Diagnostic`) is the weaker
  alternative and is justified only by a human-facing question nobody has
  asked yet.
- Disk speed: see Q2 — no shape at all.

Docs impact: none. `docs/architecture/auto-routing.md` already labels RAM and
disk-speed probing **Planned** and states `EnvironmentInfo` carries only
`backends`, `installed_extras`, `vram_budget_gb`, `offline` — still true after
this note (a gitignored design doc changes no shipped claim).

## 4. Decision line for pc-1

1. **RAM — DEFER** (design above; trigger: a backend declares a RAM floor a
   rule reads; VRAM-adjacency is speculative today).
2. **Disk speed — DROP** (side-effect probe; opt-in diagnostics only if a
   consumer ever appears; capacity already handled by `shutil.disk_usage`).
3. **Flow — keep Planned** (no fields, no diagnostics; ship probe + field +
   rule atomically at trigger time).
