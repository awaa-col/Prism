# -*- coding: utf-8 -*-
import os
from pathlib import Path
from typing import Dict, Any, List
import logging

from app.plugins.interface import PluginInterface, PluginMetadata
from fastapi.responses import HTMLResponse

# This plugin's directory
BASE_DIR = Path(__file__).resolve().parent
# It's good practice for plugins to create their own logger.
logger = logging.getLogger(__name__)

class OAuthKeyFrontendPlugin(PluginInterface):
    """
    A simple plugin that serves a static HTML file to provide a UI for testing
    user-facing OAuth and API key management endpoints.
    """

    def get_metadata(self) -> PluginMetadata:
        """Returns metadata about the plugin."""
        return PluginMetadata(
            name="OAuth & Key Frontend",
            version="1.0.0",
            author="白岚",
            description="Serves a UI to test OAuth and API Key self-service.",
        )

    async def initialize(self):
        """Initializes the plugin."""
        logger.info("OAuth & Key Frontend Plugin initialized.")
        self._html_content = ""
        try:
            # Pre-load the HTML file content
            html_file_path = BASE_DIR / "static" / "index.html"
            with open(html_file_path, "r", encoding="utf-8") as f:
                self._html_content = f.read()
        except Exception as e:
            logger.error(f"Failed to load index.html: {e}", exc_info=True)

    async def shutdown(self):
        """Called when the plugin is unloaded."""
        logger.info("OAuth & Key Frontend Plugin shut down.")
        self._html_content = ""

    def get_route_schema(self) -> List[Dict[str, Any]]:
        """
        Defines the API endpoint this plugin exposes.
        It serves the main UI.
        """
        return [
            {
                "method": "GET",
                "path": "/ui",
                "summary": "Get OAuth/Key Test UI",
                "description": "Returns the main HTML page for testing user features.",
                "tags": ["UI"],
                "response_class": "HTMLResponse"
            }
        ]

    async def invoke(self, method_name: str, payload: Dict[str, Any]) -> Any:
        """Handles the request to the endpoint defined in get_route_schema."""
        if method_name == "GET:/ui":
            if not self._html_content:
                return HTMLResponse(content="<h1>Error: Plugin UI file not loaded.</h1>", status_code=500)
            return HTMLResponse(content=self._html_content)
        
        return HTMLResponse(content="<h1>Not Found</h1>", status_code=404)