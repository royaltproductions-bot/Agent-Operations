#!/usr/bin/env python3
"""Legacy compatibility wrapper. Render operations now run directly in GitHub.

The former ops/render-deploy-requests / ChatGPT watchdog queue is retired.
"""
from tools.render_direct_api import (
    SERVICE_ID, WORKSPACE_ID, PUBLISH_DIR as SITE_PATH,
    render_status, deploy_site_if_needed,
)

def request_render_deployment():
    """Compatibility with old callers: execute actual Render REST API, not a request."""
    return deploy_site_if_needed()
