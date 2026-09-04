from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent


class VtNoteBuildPy(build_py):
    def run(self) -> None:
        frontend = ROOT / "frontend"
        npm = "npm.cmd" if os.name == "nt" else "npm"
        staging_parent = ROOT / ".vtnote" / "Cache" / "package" / "frontend"
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=staging_parent,
            ignore_cleanup_errors=True,
        ) as temporary:
            staged_frontend = Path(temporary) / "frontend"
            shutil.copytree(
                frontend,
                staged_frontend,
                ignore=shutil.ignore_patterns("node_modules", "dist"),
            )
            subprocess.run([npm, "ci"], cwd=staged_frontend, check=True)
            subprocess.run([npm, "run", "build"], cwd=staged_frontend, check=True)
            super().run()

            package_root = Path(self.build_lib) / "vtnote"
            web_target = package_root / "web"
            resource_target = package_root / "resources"
            shutil.rmtree(web_target, ignore_errors=True)
            shutil.rmtree(resource_target, ignore_errors=True)
            shutil.copytree(staged_frontend / "dist", web_target)
            shutil.copytree(ROOT / "assets", resource_target)


setup(cmdclass={"build_py": VtNoteBuildPy})
