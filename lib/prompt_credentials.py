"""GUI/CLI prompts for SonarQube URL and token."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class CredentialInput:
    url: str
    token: str
    cancelled: bool = False


def _zenity_forms(title: str, text: str, url_default: str = "") -> CredentialInput | None:
    if not shutil.which("zenity") or not os.environ.get("DISPLAY"):
        return None
    cmd = [
        "zenity",
        "--forms",
        "--title=" + title,
        "--text=" + text,
        "--add-entry=SonarQube URL",
        "--add-password=User token",
        f"--entry-text={url_default}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return CredentialInput(url="", token="", cancelled=True)
    lines = result.stdout.strip().split("|", 1)
    url = lines[0].strip() if lines else ""
    token = lines[1].strip() if len(lines) > 1 else ""
    return CredentialInput(url=url, token=token)


def _kdialog_forms(title: str, text: str, url_default: str = "") -> CredentialInput | None:
    if not shutil.which("kdialog") or not os.environ.get("DISPLAY"):
        return None
    script = f"""
URL=$(kdialog --title "{title}" --inputbox "{text}\\n\\nSonarQube URL:" "{url_default}")
test $? -eq 0 || exit 1
TOKEN=$(kdialog --title "{title}" --password "User token:")
test $? -eq 0 || exit 1
printf '%s|%s' "$URL" "$TOKEN"
"""
    try:
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return CredentialInput(url="", token="", cancelled=True)
    parts = result.stdout.strip().split("|", 1)
    return CredentialInput(
        url=parts[0].strip() if parts else "",
        token=parts[1].strip() if len(parts) > 1 else "",
    )


def _tkinter_forms(title: str, text: str, url_default: str = "") -> CredentialInput | None:
    if not os.environ.get("DISPLAY"):
        return None
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(title, text, parent=root)
    url = simpledialog.askstring(title, "SonarQube URL:", initialvalue=url_default, parent=root)
    if url is None:
        root.destroy()
        return CredentialInput(url="", token="", cancelled=True)
    token = simpledialog.askstring(title, "User token:", show="*", parent=root)
    root.destroy()
    if token is None:
        return CredentialInput(url="", token="", cancelled=True)
    return CredentialInput(url=url.strip(), token=token.strip())


def _cli_forms(title: str, text: str, url_default: str = "") -> CredentialInput:
    if sys.stdin.isatty():
        print(f"\n=== {title} ===\n{text}\n", file=sys.stderr)
        url = input(f"SonarQube URL [{url_default}]: ").strip() or url_default
        token = input("User token (input hidden): ").strip()
        if not token:
            try:
                import getpass

                token = getpass.getpass("User token: ").strip()
            except (ImportError, OSError):
                pass
        if not url or not token:
            return CredentialInput(url=url, token=token, cancelled=True)
        return CredentialInput(url=url, token=token)
    return CredentialInput(url="", token="", cancelled=True)


def notify(title: str, message: str, *, level: str = "warning") -> None:
    if shutil.which("zenity") and os.environ.get("DISPLAY"):
        flag = {"warning": "--warning", "error": "--error", "info": "--info"}.get(level, "--info")
        subprocess.run(["zenity", flag, "--title=" + title, "--text=" + message], check=False)
        return
    if shutil.which("kdialog") and os.environ.get("DISPLAY"):
        flag = {"warning": "--sorry", "error": "--error", "info": "--msgbox"}.get(level, "--msgbox")
        subprocess.run(["kdialog", flag, message, "--title", title], check=False)
        return
    print(f"{title}: {message}", file=sys.stderr)


def prompt_credentials(
    *,
    title: str = "SonarQube Credentials",
    reason: str = "Enter your SonarQube server URL and user token.",
    url_default: str = "",
    prefer_cli: bool = False,
    fallback_cli_on_cancel: bool = True,
) -> CredentialInput:
    if os.environ.get("SFT_SONAR_NO_PROMPT") == "1":
        return CredentialInput(url="", token="", cancelled=True)
    if prefer_cli or os.environ.get("SFT_SONAR_CLI_PROMPT") == "1":
        return _cli_forms(title, reason, url_default)

    for fn in (_zenity_forms, _kdialog_forms, _tkinter_forms):
        result = fn(title, reason, url_default)
        if result is not None:
            if result.cancelled and fallback_cli_on_cancel and sys.stdin.isatty():
                print("Dialog cancelled — enter credentials in the terminal:", file=sys.stderr)
                return _cli_forms(title, reason, url_default)
            return result
    return _cli_forms(title, reason, url_default)


def prompt_unreachable(
    url: str,
    detail: str,
    *,
    url_default: str | None = None,
    prefer_cli: bool = False,
) -> CredentialInput:
    notify(
        "SonarQube Unreachable",
        f"Cannot reach {url} or the token was rejected.\n\n{detail}\n\nPlease update your credentials.",
        level="warning",
    )
    return prompt_credentials(
        title="Update SonarQube Credentials",
        reason=f"Server issue: {detail}",
        url_default=url_default or url,
        prefer_cli=prefer_cli,
    )
