"""Small, free-only provider router for Gambit's actual execution resources.

Add a provider in a trusted `tools.gambit_plugin_*` module via register().
Gambit's worker invokes a capability, not a named vendor or hard-coded API.
Provider code owns its authentication and response mapping, and MUST return
the exact capability's ordinary Python output shape. No provider is charged
or silently substituted when its requirements are unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
import re
from typing import Callable


class ResourceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    capability: str
    available: Callable[[], bool]
    invoke: Callable[..., object]
    priority: int = 0
    zero_cost: bool = True


_PROVIDERS: dict[str, list[Provider]] = {}
_LOADED = False


def register(provider: Provider):
    if not provider.zero_cost:
        raise ValueError("paid_gambit_resource_forbidden")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,60}", provider.name):
        raise ValueError("invalid_resource_name")
    if not re.fullmatch(r"[a-z][a-z_]{2,40}", provider.capability):
        raise ValueError("invalid_resource_capability")
    existing = _PROVIDERS.setdefault(provider.capability, [])
    if any(item.name == provider.name for item in existing):
        raise ValueError("duplicate_resource_name")
    existing.append(provider)
    existing.sort(key=lambda item: item.priority, reverse=True)


def _browser_available():
    import importlib.util
    import shutil
    return (importlib.util.find_spec("playwright") is not None
            and importlib.util.find_spec("PIL") is not None
            and (shutil.which("google-chrome") is not None or
                 shutil.which("chromium") is not None))


def _load_defaults():
    from tools import gambit_site_resources, gambit_research, gambit_visual_qa
    register(Provider("public-page-search", "public_search", lambda: True,
                      lambda *a, **k: gambit_research.search_public_web(*a, **k)))
    register(Provider("landwind-static-source", "site_source",
                      gambit_site_resources.resources_available,
                      lambda *a, **k: gambit_site_resources.create_project(*a, **k)))
    register(Provider("desktop-mobile-browser", "site_visual", _browser_available,
                      lambda *a, **k: gambit_visual_qa.render_browser_evidence(*a, **k)))


def _load():
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    _load_defaults()
    # Only explicitly configured modules shipped with this repository can add
    # integrations. Untrusted buyer content cannot register an API destination.
    for module in filter(None, (s.strip() for s in
                                os.getenv("GAMBIT_RESOURCE_PLUGINS", "").split(","))):
        if not re.fullmatch(r"tools\.gambit_plugin_[a-z0-9_]+", module):
            raise ValueError("untrusted_resource_module")
        importlib.import_module(module)


def available(capability: str) -> bool:
    _load()
    return any(provider.available() for provider in _PROVIDERS.get(capability, []))


def execute(capability: str, *args, **kwargs):
    _load()
    for provider in _PROVIDERS.get(capability, []):
        if provider.available():
            result = provider.invoke(*args, **kwargs)
            # Validate the shared contract at the integration boundary, not
            # just inside a vendor-specific implementation.
            if capability == "site_source" and not (
                    isinstance(result, tuple) and len(result) == 3 and
                    isinstance(result[0], bytes) and len(result[0]) > 100 and
                    isinstance(result[1], str) and
                    isinstance(result[2], dict)):
                raise ValueError("site_provider_invalid_output")
            if capability == "site_visual" and not (
                    isinstance(result, dict) and
                    {"desktop", "mobile"}.issubset(result)):
                raise ValueError("site_visual_invalid_output")
            if capability == "public_search" and not (
                    isinstance(result, dict) and
                    isinstance(result.get("results"), list)):
                raise ValueError("public_search_invalid_output")
            return result
    raise ResourceUnavailable("resource_unavailable_" + capability)


def manifest():
    _load()
    return [{"capability": capability,
             "providers": [p.name for p in providers if p.available()],
             "available": any(p.available() for p in providers),
             "billing": "no_charge"}
            for capability, providers in sorted(_PROVIDERS.items())]
