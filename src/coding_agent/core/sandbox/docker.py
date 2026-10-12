import io
import tarfile
from typing import Any

import docker
from loguru import logger

from coding_agent.config import settings


class DockerSandbox:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                self._client = docker.DockerClient(base_url=settings.DOCKER_HOST)
                self._ensure_image()
            except Exception as e:
                logger.warning(f"Docker not available: {e}")
                raise
        return self._client

    def _ensure_image(self):
        try:
            self.client.images.get(settings.SANDBOX_IMAGE)
        except docker.errors.ImageNotFound:
            logger.info(f"Pulling sandbox image: {settings.SANDBOX_IMAGE}")
            self.client.images.pull(settings.SANDBOX_IMAGE)

    def run_command(
        self,
        command: str,
        cwd: str = "/workspace",
        files: dict[str, str] | None = None,
        timeout: int = None,
    ) -> dict[str, Any]:
        timeout = timeout or settings.SANDBOX_TIMEOUT

        try:
            container = self.client.containers.create(
                image=settings.SANDBOX_IMAGE,
                command=["sleep", "infinity"],
                working_dir=cwd,
                mem_limit=settings.SANDBOX_MEMORY_LIMIT,
                cpu_quota=int(settings.SANDBOX_CPU_LIMIT * 100000),
                cpu_period=100000,
                network_disabled=True,
                remove=True,
                detach=True,
            )
        except Exception as e:
            logger.error(f"Failed to create container: {e}")
            return {"error": f"Docker unavailable: {e}", "success": False}

        try:
            container.start()

            if files:
                self._copy_files_to_container(container, files, cwd)

            exec_result = container.exec_run(
                command,
                workdir=cwd,
                demux=True,
                tty=False,
            )

            stdout = exec_result.output[0].decode() if exec_result.output[0] else ""
            stderr = exec_result.output[1].decode() if exec_result.output[1] else ""

            return {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": exec_result.exit_code,
                "success": exec_result.exit_code == 0,
            }
        except Exception as e:
            logger.error(f"Sandbox error: {e}")
            return {"error": str(e), "success": False}
        finally:
            try:
                container.stop(timeout=5)
            except:
                pass

    def _copy_files_to_container(self, container, files: dict[str, str], cwd: str):
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for path, content in files.items():
                data = content.encode()
                tarinfo = tarfile.TarInfo(name=path)
                tarinfo.size = len(data)
                tar.addfile(tarinfo, io.BytesIO(data))
        tar_stream.seek(0)
        container.put_archive(cwd, tar_stream)


# Lazy initialization - don't create sandbox at import time
_sandbox_instance = None


def get_sandbox() -> DockerSandbox:
    global _sandbox_instance
    if _sandbox_instance is None:
        _sandbox_instance = DockerSandbox()
    return _sandbox_instance
