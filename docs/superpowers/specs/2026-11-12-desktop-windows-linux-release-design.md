# Desktop releases for Windows and Linux — design

Date: 2026-11-12 · Status: draft for review

## Why

Crucible Desktop ships only for Apple Silicon (v0.9.2, v0.9.4). Windows and Linux x64 already
build, package and pass the local-voice smoke test in CI (`voice-platforms.yml`), but nothing
turns those builds into a release a person can download, verify and keep up to date. This
design adds Windows x64 and Linux x64 to the same GitHub release as the Mac build.

## What exists today

| Piece | macOS (Apple Silicon) | Windows x64 / Linux x64 |
|---|---|---|
| App packaging (`apps/desktop/package.mjs`) | `.app` in a `.zip`, ad-hoc sealed | portable folder in `.zip` / `.tar.gz` (preview only) |
| Release candidate workflow (`desktop-package.yml`) | yes, `macos-15` | no |
| Runtime staging (`scripts/release/desktop-runtime-release.py`) | Apple Silicon only | no |
| Release bundle + checks (`desktop-release-bundle.mjs`) | `codesign` verification, Mac evidence | no |
| Signed update metadata (`crucible-desktop-update-<target>.json`) | yes | format already per target |
| In-app install of an update (`updates.ts`) | replaces the `.app`, verifies the seal | `installable()` is false |
| Vendor runtime assets on v0.8.1 (uv, ripgrep, rust-analyzer, gopls, JRE, jdtls) | yes | yes (`win32-x64`, `linux-x64`) |
| Local voice | CoreML helpers | portable whisper + sherpa-onnx Pocket, CI-verified |

## Goals

- One GitHub release per desktop version carrying all three platforms, each with its own
  archive, checksum, runtime manifest and Ed25519-signed update metadata.
- A person on Windows or Linux can download, verify (checksum), run Setup and use voice.
- **Settings › Updates** on Windows and Linux tells the user a newer version exists and links
  to it, after verifying the signed metadata.
- The private update key stays off CI; signing stays a local step, as on the Mac.

## Non-goals (this design)

- In-app installation of an update on Windows or Linux (replacing a running portable folder
  needs a separate helper per OS; notify-and-download is enough first).
- Windows Authenticode signing or Microsoft Store/MSIX, Linux distro packages (deb/rpm),
  Flatpak/Snap, ARM64 Windows/Linux.
- Intel Macs.

## Decisions (my calls; flagged for review)

1. **Formats: portable archives, no installers.** Windows: `crucible-<v>-win32-x64.zip`
   containing a `Crucible` folder with `Crucible.exe`. Linux: `crucible-<v>-linux-x64.tar.gz`
   with `crucible` inside. This is what `package.mjs` already produces, so the release path
   adds verification and signing rather than new packaging. An NSIS installer or AppImage can
   come later without changing the update metadata.
2. **Unsigned on Windows, with an honest note.** Without a paid code-signing certificate,
   SmartScreen shows "Windows protected your PC" on first launch. INSTALL.txt and the release
   notes say so and show "More info → Run anyway", mirroring the Mac "Open Anyway" note.
   Authenticode can be added later; it does not affect the Ed25519 update metadata.
3. **Updates: notify, don't install.** On Windows/Linux the updater verifies the signed
   metadata for its own target and offers "Download from GitHub" (the release page) instead of
   "Install". `installable()` keeps returning false there; no new install code runs.
4. **CI: a three-platform matrix in `desktop-package.yml`.** `macos-15` (unchanged),
   `windows-latest`, `ubuntu-22.04` (glibc 2.35, same as voice CI). Each job builds the app,
   the voice payload (`build:voice` + Pocket on Mac, `build:voice:portable` elsewhere), the
   indexer for its platform, stages its runtime, packages, and uploads one candidate artifact.
   Nothing in CI holds the private key or publishes.
5. **Runtime staging generalised per target.** `desktop-runtime-release.py` takes
   `--target darwin-arm64|win32-x64|linux-x64`, restages that target's vendor assets from the
   pinned vendor release, and names the indexer asset `crucible-indexer-<target>[.exe]`. The
   agentd wheel stays one shared `any` asset. All three runtime manifests point at the same
   release tag.
6. **Release bundle split into a per-platform verifier.** `desktop-release-bundle.mjs` keeps
   the Mac path (codesign) and adds a Windows/Linux path that verifies the archive against
   the package evidence (file manifest and checksums) without codesign. Signing the metadata
   (`desktop-update-manifest.mjs`) is already platform-independent and runs on the Mac.
7. **Packaging guard for voice on every release target.** `missingReleaseVoice` extends to
   `win32-x64` and `linux-x64` (whisper helper, sherpa-onnx, Pocket worker), so a release can
   never silently ship without voice on any platform (the 0.9.2 lesson).
8. **One tag, assets merged locally.** The three candidate artifacts are downloaded, each
   verified and signed on the Mac, and uploaded to one `v<version>` release. Asset names are
   per target, so nothing collides. The extension workflow keeps excluding desktop tags.

## Release checklist (per version, all platforms)

1. Run `desktop-package.yml` with the version; all three jobs green.
2. Download the three candidates (resume-capable `curl -C -`).
3. Per platform: verify the archive against its evidence; check the voice payload is present;
   sign `desktop-update-metadata.json` → `crucible-desktop-update-<target>.json`.
4. Verify each runtime manifest's own and vendor checksums against the release assets.
5. `gh release create v<version> --latest` with every platform's assets, notes and INSTALL.txt.
6. Check `releases/latest/download/crucible-desktop-update-<target>.json` for each target.

## Open questions for the user

- Is notify-and-download acceptable for Windows/Linux updates for now, or is in-place update
  a requirement for the first Windows/Linux release?
- Is an unsigned Windows build (SmartScreen warning) acceptable, or should a code-signing
  certificate be bought before the first Windows release?

## Testing

- Unit: runtime staging per target; per-platform verifier (good archive, tampered file,
  missing voice); updater offers download, never install, on Windows/Linux targets.
- CI: the matrix itself (it runs the desktop tests and the voice smoke test on each OS).
- Manual before the first release: run the downloaded archive on a real Windows 11 machine
  and a real Ubuntu desktop (Setup, a chat turn, a spoken exchange, Settings › Updates).
  CI cannot cover the SmartScreen prompt, microphone permission or desktop integration.
