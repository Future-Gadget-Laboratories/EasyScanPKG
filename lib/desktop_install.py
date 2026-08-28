"""Install EasyScan desktop launcher + Linux Mint (Cinnamon) favorites pin."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


DESKTOP_ID = "easyscan.desktop"
ICON_NAME = "sft-sonar"  # keep existing icon asset name
APP_NAME = "EasyScan"


def _bridge_root() -> Path:
    return Path(os.environ.get("BRIDGE") or Path(__file__).resolve().parents[1])


def _applications_dir() -> Path:
    return Path.home() / ".local" / "share" / "applications"


def _icons_dir() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"


def install_icon(bridge: Path | None = None) -> Path:
    bridge = bridge or _bridge_root()
    src = bridge / "assets" / "sft-sonar.svg"
    dest_dir = _icons_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{ICON_NAME}.svg"
    if src.is_file():
        shutil.copy2(src, dest)
    return dest


def install_desktop_entry(
    *,
    bridge: Path | None = None,
    workspace: str | None = None,
    also_desktop_shortcut: bool = True,
) -> Path:
    bridge = bridge or _bridge_root()
    install_icon(bridge)
    launcher = bridge / "bin" / "sonar-desktop"
    launcher.chmod(launcher.stat().st_mode | 0o111)

    apps = _applications_dir()
    apps.mkdir(parents=True, exist_ok=True)
    dest = apps / DESKTOP_ID

    exec_line = f'"{launcher}"'
    if workspace:
        exec_line += f' --workspace "{workspace}"'

    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={APP_NAME}
GenericName=EasyScanPKG Local Sonar Launcher
Comment=Start local SonarQube, wire Cursor MCP, and open your project
Exec={exec_line}
Icon={ICON_NAME}
Terminal=false
Categories=Development;IDE;Programming;
Keywords=sonar;sonarqube;cursor;easyscan;quality;
StartupNotify=true
"""
    dest.write_text(content, encoding="utf-8")
    dest.chmod(0o755)

    # Refresh desktop database so Mint menu picks it up
    update = shutil.which("update-desktop-database")
    if update:
        subprocess.run([update, str(apps)], capture_output=True, check=False)

    # Remove legacy desktop id if present
    legacy = apps / "sft-sonar.desktop"
    if legacy.is_file() and legacy != dest:
        try:
            legacy.unlink()
        except OSError:
            pass

    if also_desktop_shortcut:
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            shortcut = desktop / DESKTOP_ID
            shutil.copy2(dest, shortcut)
            shortcut.chmod(0o755)
            legacy_desk = desktop / "sft-sonar.desktop"
            if legacy_desk.is_file():
                try:
                    legacy_desk.unlink()
                except OSError:
                    pass
            # Mark as trusted for Cinnamon/Nemo (ignore failures)
            subprocess.run(
                ["gio", "set", str(shortcut), "metadata::trusted", "true"],
                capture_output=True,
                check=False,
            )

    return dest


def _get_favorites() -> list[str] | None:
    for schema in ("org.cinnamon", "org.gnome.shell"):
        try:
            result = subprocess.run(
                ["gsettings", "get", schema, "favorite-apps"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            continue
        if result.returncode != 0:
            continue
        raw = result.stdout.strip()
        if not raw.startswith("["):
            continue
        # Parse GVariant string array: ['a.desktop', 'b.desktop']
        inner = raw.strip("[]")
        if not inner.strip():
            return []
        items = []
        for part in inner.split(","):
            part = part.strip().strip("'").strip('"')
            if part:
                items.append(part)
        return items
    return None


def _set_favorites(items: list[str]) -> bool:
    quoted = ", ".join(f"'{x}'" for x in items)
    value = f"[{quoted}]"
    for schema in ("org.cinnamon", "org.gnome.shell"):
        result = subprocess.run(
            ["gsettings", "set", schema, "favorite-apps", value],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def add_to_favorites() -> bool:
    current = _get_favorites()
    if current is None:
        return False
    if DESKTOP_ID in current:
        return True
    return _set_favorites([*current, DESKTOP_ID])


def install_desktop(
    *,
    bridge: Path | None = None,
    workspace: str | None = None,
    favorites: bool = True,
    desktop_shortcut: bool = True,
) -> dict:
    bridge = bridge or _bridge_root()
    entry = install_desktop_entry(
        bridge=bridge,
        workspace=workspace,
        also_desktop_shortcut=desktop_shortcut,
    )
    pinned = add_to_favorites() if favorites else False
    return {
        "desktop_entry": str(entry),
        "favorites_pinned": pinned,
        "icon": str(_icons_dir() / f"{ICON_NAME}.svg"),
        "workspace": workspace,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace")
    parser.add_argument("--no-favorites", action="store_true")
    parser.add_argument("--no-desktop-shortcut", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = install_desktop(
        workspace=args.workspace,
        favorites=not args.no_favorites,
        desktop_shortcut=not args.no_desktop_shortcut,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Desktop entry: {result['desktop_entry']}")
        print(f"Favorites pinned: {result['favorites_pinned']}")
