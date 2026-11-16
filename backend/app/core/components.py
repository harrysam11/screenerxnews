from typing import Dict, Any
from app.core.config import settings

class OptionalComponentsManager:
    """
    Manages optional system integrations for the Multi-Chain Memecoin OS.
    In the lightweight / Termux profile (P10.2-B), all optional integrations default
    to disabled, allowing the core FastAPI application and SQLite database to run
    100% self-contained without Docker, PostgreSQL, Redis, n8n, Windmill, or Hermes.
    """

    COMPONENTS = {
        "docker": {
            "name": "Docker Container Runtime",
            "description": "Container virtualization for hosting full-stack services",
            "setting_attr": "ENABLE_DOCKER",
            "architecture_role": "INFRASTRUCTURE"
        },
        "windmill": {
            "name": "Windmill Engine",
            "description": "Primary high-performance Python worker execution for data pipelines, discovery, and cron jobs",
            "setting_attr": "ENABLE_WINDMILL",
            "architecture_role": "PRIMARY"
        },
        "hermes": {
            "name": "Hermes AI Assistant",
            "description": "Optional research analyst for cross-system intelligence and digest generation",
            "setting_attr": "ENABLE_HERMES",
            "architecture_role": "OPTIONAL"
        },
        "n8n": {
            "name": "n8n Workflow Automation",
            "description": "Deferred visual workflow engine (not required for Version 1 Core OS)",
            "setting_attr": "ENABLE_N8N",
            "architecture_role": "DEFERRED"
        },
        "redis": {
            "name": "Redis In-Memory Cache",
            "description": "Optional high-throughput shared caching and pub/sub message broker",
            "setting_attr": "ENABLE_REDIS",
            "architecture_role": "OPTIONAL"
        }
    }

    @classmethod
    def is_enabled(cls, component_id: str) -> bool:
        comp = cls.COMPONENTS.get(component_id)
        if not comp:
            return False
        return getattr(settings, comp["setting_attr"], False)

    @classmethod
    def are_all_optional_disabled(cls) -> bool:
        return not any(cls.is_enabled(comp_id) for comp_id in cls.COMPONENTS)

    @classmethod
    def get_status_report(cls) -> Dict[str, Any]:
        components_state = {}
        for comp_id, info in cls.COMPONENTS.items():
            components_state[comp_id] = {
                "name": info["name"],
                "description": info["description"],
                "enabled": cls.is_enabled(comp_id)
            }

        all_disabled = cls.are_all_optional_disabled()
        return {
            "profile": settings.PROFILE,
            "operating_mode": settings.OPERATING_MODE,
            "all_optional_disabled": all_disabled,
            "core_operational": True,
            "components": components_state
        }

components_manager = OptionalComponentsManager()
