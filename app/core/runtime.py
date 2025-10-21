from typing import Optional

from app.plugins.loader import PluginLoader
from app.core.chain_runner import ChainRunner


_plugin_loader: Optional[PluginLoader] = None
_chain_runner: Optional[ChainRunner] = None


def set_plugin_loader(loader: PluginLoader) -> None:
    global _plugin_loader
    _plugin_loader = loader


def get_plugin_loader() -> Optional[PluginLoader]:
    return _plugin_loader


def set_chain_runner(runner: ChainRunner) -> None:
    global _chain_runner
    _chain_runner = runner


def get_chain_runner() -> Optional[ChainRunner]:
    return _chain_runner 