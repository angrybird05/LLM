# tools package
import coding_agent.core.tools.edit  # noqa: F401

# Import tool modules to trigger their registration with the registry.
# The `bash` tool backend is chosen by SHELL_MODE: local host shell (default)
# or the Docker sandbox for untrusted code.
import coding_agent.core.semantic.tools  # noqa: F401
import coding_agent.core.tools.filesystem  # noqa: F401
import coding_agent.core.tools.task  # noqa: F401
import coding_agent.core.tools.web  # noqa: F401
from coding_agent.config import settings
from coding_agent.core.tools.base import READ_ONLY_TOOL_NAMES, SEMANTIC_TOOL_NAMES, registry

if settings.SHELL_MODE == "docker":
    import coding_agent.core.tools.shell  # noqa: F401
else:
    import coding_agent.core.tools.shell_local  # noqa: F401

__all__ = ["registry", "READ_ONLY_TOOL_NAMES"]
