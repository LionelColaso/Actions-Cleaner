# TODO — Actions-Cleaner

Remaining unimplemented items and open questions as of 2026-10-25. All core features, tests, CI, builds, documentation, and the module split are complete (see git history).

## Open

### Build & Distribution

- [ ] **Add Windows installer** — NSIS or WiX installer for Nuitka build (start menu, uninstaller, file association)
- [ ] **Add macOS DMG** — `create-dmg` for notarized app bundle
- [ ] **Add Linux AppImage/Flatpak** — `appimage-builder` or `flatpak-builder` configs
- [ ] **Sign binaries** — Code signing for Windows/macOS (requires certificates)

### Documentation

- [ ] **Add README screenshots** — Annotated UI screenshots for each feature

### CI/CD

- [ ] **Add release automation** — `release.yml` already exists; verify it creates signed artifacts

(End of file)