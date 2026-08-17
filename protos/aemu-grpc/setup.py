"""
    Setup file for aemu-grpc.
    Use setup.cfg / pyproject.toml to configure your project.
"""
import os
import subprocess
import sys
from os import path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop


def compile_protos():
    here = path.abspath(path.dirname(__file__))
    services_dir = path.abspath(path.join(here, "..", "services"))
    if not path.exists(services_dir):
        sys.stderr.write(f"Services proto directory not found at {services_dir}\n")
        return

    outdir = path.join(here, "src", "aemu", "proto")
    os.makedirs(outdir, exist_ok=True)
    init_file = path.join(outdir, "__init__.py")
    if not path.exists(init_file):
        open(init_file, "w").close()

    include_flags = [f"-I{services_dir}"]
    proto_files = []
    for root, dirs, files in os.walk(services_dir):
        include_flags.append(f"-I{root}")
        for f in sorted(files):
            if f.endswith(".proto"):
                proto_files.append(path.join(root, f))

    if not proto_files:
        sys.stderr.write("No .proto files found to compile.\n")
        return

    sys.stderr.write(f"Compiling {len(proto_files)} .proto files into {outdir}...\n")
    try:
        cmd = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            *include_flags,
            f"--python_out={outdir}",
            f"--grpc_python_out={outdir}",
            *proto_files,
        ]
        subprocess.check_call(cmd)
    except Exception as err:
        sys.stderr.write(
            f"Failed with grpc_tools ({err}), trying system protoc CLI...\n"
        )
        cmd = [
            "protoc",
            *include_flags,
            f"--python_out={outdir}",
            f"--grpc_python_out={outdir}",
            *proto_files,
        ]
        subprocess.check_call(cmd)


class ProtoBuild(build_py):
    """Automatically compiles all .proto files from ../services before packaging."""

    def run(self):
        compile_protos()
        super().run()


class ProtoDevelop(develop):
    """Automatically compiles all .proto files during editable install (pip install -e .)."""

    def run(self):
        compile_protos()
        super().run()


if __name__ == "__main__":
    setup(
        cmdclass={
            "build_py": ProtoBuild,
            "develop": ProtoDevelop,
        },
    )
