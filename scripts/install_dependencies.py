#!/usr/bin/env python3
"""Install project dependencies with one OpenCV distribution.

pdf2zh/BabelDOC require opencv-python-headless while RapidOCR currently
declares opencv-python. Both distributions install the same ``cv2`` module,
so installing the requirements verbatim leaves two wheels overwriting one
another. This installer lets pip resolve the normal dependency graph first,
then removes every OpenCV wheel and reinstalls only the headless provider.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path


HEADLESS = "opencv-python-headless"
OTHER_OPENCV = (
    "opencv-python",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)


def _run(*args: str) -> None:
    command = [sys.executable, "-m", "pip", *args]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _installed_opencv() -> list[str]:
    names = [HEADLESS, *OTHER_OPENCV]
    return [name for name in names if _installed_version(name) is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements.txt",
        help="requirements file to install",
    )
    args = parser.parse_args()

    _run("install", "-r", str(args.requirements))

    headless_version = _installed_version(HEADLESS)
    _run("uninstall", "-y", HEADLESS, *OTHER_OPENCV)
    _run(
        "install",
        f"{HEADLESS}=={headless_version}" if headless_version else f"{HEADLESS}>=4.10.0.84",
    )

    providers = _installed_opencv()
    if providers != [HEADLESS]:
        raise RuntimeError(
            "Expected only opencv-python-headless, found: "
            + (", ".join(providers) or "none")
        )

    import cv2

    print(f"OpenCV provider: {HEADLESS} {headless_version or cv2.__version__}")
    print(f"cv2 version: {cv2.__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
