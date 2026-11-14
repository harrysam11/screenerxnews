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
            "setting_attr": "ENABLE_DOCKER"
        },
        "n8n": {
            "name": "n8n Workflow Automation",
            "description": "Visual low-code orchestration for event webhooks and notifications",
            "setting_attr": "ENABLE_N8N"
        },
        "windmill": {
            "name": "Windmill Engine",
            "description": "High-performance Python worker execution for data pipelines and cron jobs",
            "setting_attr": "ENABLE_WINDMILL"
        },
        "hermes": {
            "name": "Hermes AI Assistant",
            "description": "Cross-system LLM intelligence and conversational query dispatcher",
            "setting_attr": "ENABLE_HERMES"
        },
        "redis": {
            "name": "Redis In-Memory Cache",
            "description": "High-throughput shared caching and pub/sub message broker",
            "setting_attr": "ENABLE_REDIS"
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
