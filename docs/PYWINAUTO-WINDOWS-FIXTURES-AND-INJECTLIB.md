# pywinauto Windows fixtures and `injectlib` assessment

**Research checked:** 2026-10-18. Upstream source and Windows compile compatibility
were checked. No GUI fixture run, process injection, or live Camfrog interaction
was performed.

## Findings

### `PywinautoTestapps`

- The upstream default branch is `master` at `980a3b12109050a51e037e1a4149292e09573cec`
  (2021-02-28). GitHub lists no releases. Its `LICENSE` is MIT. [Pinned repository
 tree](https://github.com/pywinauto/PywinautoTestapps/tree/980a3b12109050a51e037e1a4149292e09573cec),
  [license](https://github.com/pywinauto/PywinautoTestapps/blob/980a3b12109050a51e037e1a4149292e09573cec/LICENSE),
  [release page](https://github.com/pywinauto/PywinautoTestapps/releases).
- The solution includes native MFC/Win32 control samples (`CmnCtrl1`, `CmnCtrl2`,
  `NewControls`), a WPF sample, tray and input testers, and a benchmark. The solution
  identifies Visual Studio 14; native projects specify toolset `v140`, while
  `WpfControls` targets .NET Framework 4.0 Client Profile and `Benchmark` targets
  .NET Framework 4.6.1. These are concrete reasons to build-check selected fixtures
  with the current Windows toolchain before relying on them; compatibility is not
  established by this review. [Solution](https://github.com/pywinauto/PywinautoTestapps/blob/980a3b12109050a51e037e1a4149292e09573cec/TestApps.sln),
  [native project](https://github.com/pywinauto/PywinautoTestapps/blob/980a3b12109050a51e037e1a4149292e09573cec/CmnCtrl1/CmnCtrl1.vcxproj),
  [WPF project](https://github.com/pywinauto/PywinautoTestapps/blob/980a3b12109050a51e037e1a4149292e09573cec/WpfControls/WpfControls.csproj),
  [benchmark project](https://github.com/pywinauto/PywinautoTestapps/blob/980a3b12109050a51e037e1a4149292e09573cec/Benchmark/Benchmark/Benchmark.csproj).
- On the available Windows 10.0.26300 host, `WpfControls.csproj` failed with its
  checked-in .NET Framework 4.0 Client Profile target (`MSB3644`: target reference
  assemblies unavailable). The same project compiled to
  `Release\\wpf\\WpfApplication1.exe` when MSBuild was given an uncommitted
  `TargetFrameworkVersion=v4.8` and empty `TargetFrameworkProfile` override. This
  proves a local retargeted compile only; it does not prove the original project
  builds unchanged or that the window works under automation. The host has VS 18
  Build Tools/MSVC 14.51 but no MFC headers, so the MFC samples cannot be built
  with the installed components. No upstream or ZChat source was modified for
  this check.
- pywinauto documents separate `win32` and `uia` backends, and recommends choosing
  based on the target application's accessibility technology. It also documents
  process/handle attachment and that UIA elements may lack HWNDs. Thus these
  fixtures can exercise discovery and adapter behavior across technologies, but
  cannot by themselves validate Camfrog-specific selectors or submit semantics.
  [Backend selection and process attachment](https://pywinauto.readthedocs.io/en/latest/HowTo.html),
  [backend guidance](https://pywinauto.readthedocs.io/en/latest/getting_started.html).

### `injectlib`

- Upstream `master` and tag `0.0.3` both point to
  `8d0e6418116fbcfb7dee05aa9616d34dac75f78e`; commit date is 2026-10-10 and the
  GitHub release was published 2026-10-10. Its checked-in license is BSD-3-Clause.
  [Pinned repository](https://github.com/pywinauto/injectlib/tree/8d0e6418116fbcfb7dee05aa9616d34dac75f78e),
  [0.0.3 release](https://github.com/pywinauto/injectlib/releases/tag/0.0.3),
  [license](https://github.com/pywinauto/injectlib/blob/8d0e6418116fbcfb7dee05aa9616d34dac75f78e/LICENSE).
- The README describes DLL injection for access to widget properties and lists
  .NET/WPF and Qt components. The API falls back to `dotnet/bootstrap` when no
  backend is registered. The Windows injector requires matching Python/target
  bitness, opens the process with `PROCESS_ALL_ACCESS`, allocates and writes
  remote memory, and starts a remote thread to load the DLL. These are source-level
  behaviors, not a claim that the library has been tested against Camfrog.
  [README](https://github.com/pywinauto/injectlib/blob/8d0e6418116fbcfb7dee05aa9616d34dac75f78e/README.md),
  [API](https://github.com/pywinauto/injectlib/blob/8d0e6418116fbcfb7dee05aa9616d34dac75f78e/src/injectlib/api.py),
  [injector](https://github.com/pywinauto/injectlib/blob/8d0e6418116fbcfb7dee05aa9616d34dac75f78e/src/injectlib/injector.py).
- The hook backend calls `SetWindowsHookEx` with thread ID zero. Microsoft documents
  that for desktop apps this associates the hook with all existing threads on the
  caller's desktop. The same documentation notes global hooks affect all apps on
  that desktop and should be restricted to special-purpose tools or debugging.
  [Upstream hook code](https://github.com/pywinauto/injectlib/blob/8d0e6418116fbcfb7dee05aa9616d34dac75f78e/backends/hook/src/winmsg_listener/dllmain.cpp),
  [Microsoft API documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw).

## ZChat relevance and recommendation

ZChat's Camfrog 8.5 findings describe a 64-bit native Win32/WTL shell and a
`CComboBoxTS` custom status control. That local UI evidence does not identify the
client as WPF or Qt, the frameworks for which `injectlib` documents property
readers. [ZChat Camfrog findings](CAMFROG-8.5-FINDINGS.md).

**Recommendation:** use a small, pinned subset of `PywinautoTestapps` as Windows
integration fixtures for ZChat's adapter (process/window ownership, control
discovery, stale HWND, target exit, and fail-closed behavior). Keep fixture source
and build tooling out of application dependencies and the shipped package. Build
the selected projects on the actual supported Windows runner first; execute GUI
tests only in a dedicated interactive test session. `SendKeysTester` and
`MouseTester` can inform input-side-effect cases, but should not drive input on a
user's production desktop. A passing fixture suite establishes adapter behavior
against those fixtures only, not Camfrog focus/ding behavior, account identity,
server-visible status, or packaged-runtime acceptance.

The repository now includes process-identity/ownership checks that run on all
platforms and Windows-only HWND integration cases in
`windows_tests/test_runtime_adapter.py`. Those tests use a small local
Tk window by default; `ZCHAT_TESTAPP_EXE` can select a prebuilt test application
for the cases that do not require closing the window while its process remains
alive. The optional upstream checkout is pinned by
`windows_tests/fixtures/pywinauto_testapps.pin` and fetched into an ignored cache.
`full-build.bat` calls `build.bat`, which runs `pytest windows_tests`; the GitHub
Windows build job therefore collects these tests. The local Linux run verifies
only process-level behavior. GUI/HWND cases are opt-in through
`ZCHAT_RUN_WINDOWS_GUI_TESTS=1`; ordinary CI and local builds leave it unset.
The hosted Windows runner did not expose the fixture's top-level HWND to
`EnumWindows`, despite the Tk process reporting ready. The hosted job therefore
leaves these interactive HWND tests disabled; it still runs the process-
identity/ownership cases. Enable the HWND cases only in a Windows session where
fixture windows are visible and enumerable.

Do **not** add `injectlib` as an application dependency or status-submit fallback
on this evidence. Its documented backends do not match the recorded WTL client,
and its source-level injection operations materially expand the target-process
and desktop impact. If future read-only research finds a supported Camfrog
framework, any experiment should remain isolated from status workers and require
separate Windows compatibility, stability, and security review. Keep
`strict_background_submit_supported` closed until the existing acceptance gates
are independently satisfied.

## Evidence boundaries

| Claim | State |
|---|---|
| Upstream commits, tag/release metadata, license files, project targets, and injection calls | Source-verified at the pinned revisions above |
| Original selected test apps build unchanged on the current Windows host | Not verified; WPF requires retargeting and MFC components are absent |
| Retargeted WPF sample compiles on the current Windows host | Verified compile only; GUI runtime not exercised |
| Process identity and ownership regression cases | Verified on Linux: 4 passed |
| ZChat adapter passes real-GUI/HWND regression tests against a controlled fixture | Implemented; not run in this environment |
| `injectlib` can inspect or submit Camfrog status correctly and without focus/audio effects | Unverified; no injection or live Camfrog test was performed |
| Camfrog provider-visible status and packaged ZChat acceptance | Not established by these upstream projects |
