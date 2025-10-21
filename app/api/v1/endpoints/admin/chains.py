from typing import List, Dict, Any, Union
import yaml
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth_deps import require_admin
from app.db.models import User
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.chains")

def _persist_routes_and_invalidate_cache():
    """Persists the current route configuration to config.yml."""
    from app.core.config import get_settings
    
    settings = get_settings()
    config_file = "config.yml"
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config_data = {}
        logger.warning(f"Config file '{config_file}' not found. A new one will be created if changes are made.")

    # Update the routes section
    config_data['routes'] = settings.routes.routes

    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, sort_keys=False, indent=2)
        logger.info("Routes persisted to config.yml.")
    except Exception as e:
        logger.error(f"Failed to persist routes to {config_file}", error=str(e))
        # In a real scenario, you might want to raise an exception or handle this more gracefully
        pass

    # 确保运行期链路缓存刷新
    from app.core.runtime import get_chain_runner
    chain_runner = get_chain_runner()
    if chain_runner:
        chain_runner.clear_cache()

def _normalize_chain_items(chain_items: List[Union[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in chain_items or []:
        if isinstance(item, str):
            normalized.append({"plugin": item})
        elif isinstance(item, dict) and "plugin" in item and isinstance(item["plugin"], str) and item["plugin"].strip():
            normalized.append(item)
    return normalized

@router.get("/")
async def list_chains(current_user: User = Depends(require_admin)):
    """List all configured plugin chains"""
    from app.core.config import get_settings
    from app.core.chain_runner import ChainRunner
    from app.main import plugin_loader
    
    settings = get_settings()
    if not plugin_loader:
        return {"chains": [], "status": "plugin_loader_not_initialized"}
    
    chain_runner = ChainRunner(plugin_loader.get_all_plugins())
    chains = [{"route": r, "chain": c.get("chain", []), "validation": chain_runner.validate_chain(r)} for r, c in settings.routes.routes.items()]
    
    logger.info("Chain list requested", chain_count=len(chains), user_id=str(current_user.id))
    return {"chains": chains, "total": len(chains), "status": "success"}

@router.put("/{route_path:path}")
async def update_chain(route_path: str, chain_config: Dict[str, Any], current_user: User = Depends(require_admin)):
    """Update plugin chain for a route"""
    from app.core.config import get_settings
    from app.main import plugin_loader
    
    if not route_path.startswith("/"):
        route_path = "/" + route_path
    
    if "chain" not in chain_config:
        raise HTTPException(status_code=400, detail="Request must include 'chain' field")
    
    chain_config["chain"] = _normalize_chain_items(chain_config.get("chain", []))
    
    if plugin_loader:
        for item in chain_config["chain"]:
            plugin_name = item.get("plugin")
            if plugin_name and not plugin_loader.get_plugin(plugin_name):
                raise HTTPException(status_code=400, detail=f"Plugin '{plugin_name}' not found")
    
    settings = get_settings()
    settings.routes.routes[route_path] = chain_config
    _persist_routes_and_invalidate_cache()
    
    logger.info("Chain updated", route=route_path, user_id=str(current_user.id))
    return {"message": f"Chain for '{route_path}' updated"}

@router.post("/")
async def create_chain(chain_data: Dict[str, Any], current_user: User = Depends(require_admin)):
    """Create a new plugin chain"""
    from app.core.config import get_settings
    from app.main import plugin_loader
    
    route_path = chain_data.get("route")
    if not route_path:
        raise HTTPException(status_code=400, detail="Route path is required")
    if not route_path.startswith("/"):
        route_path = "/" + route_path
        
    config = chain_data.get("config", {})
    if "chain" not in config:
        raise HTTPException(status_code=400, detail="Config must include 'chain' field")
        
    config["chain"] = _normalize_chain_items(config.get("chain", []))
    
    if plugin_loader:
        for item in config["chain"]:
            plugin_name = item.get("plugin")
            if plugin_name and not plugin_loader.get_plugin(plugin_name):
                raise HTTPException(status_code=400, detail=f"Plugin '{plugin_name}' not found")

    settings = get_settings()
    settings.routes.routes[route_path] = config
    _persist_routes_and_invalidate_cache()
    
    logger.info("Chain created", route=route_path, user_id=str(current_user.id))
    return {"message": f"Chain for '{route_path}' created"}

@router.delete("/{route_path:path}")
async def delete_chain(route_path: str, current_user: User = Depends(require_admin)):
    """Delete a plugin chain"""
    from app.core.config import get_settings
    
    if not route_path.startswith("/"):
        route_path = "/" + route_path
    
    settings = get_settings()
    if route_path not in settings.routes.routes:
        raise HTTPException(status_code=404, detail=f"Chain for '{route_path}' not found")
    
    del settings.routes.routes[route_path]
    _persist_routes_and_invalidate_cache()
    
    logger.info("Chain deleted", route=route_path, user_id=str(current_user.id))
    return {"message": f"Chain for '{route_path}' deleted"}

@router.get("/{route_path:path}")
async def get_chain_details(route_path: str, current_user: User = Depends(require_admin)):
    """Get detailed information about a specific chain"""
    from app.core.config import get_settings
    from app.core.chain_runner import ChainRunner
    from app.main import plugin_loader
    
    if not route_path.startswith("/"):
        route_path = "/" + route_path
    
    settings = get_settings()
    if route_path not in settings.routes.routes:
        raise HTTPException(status_code=404, detail=f"Chain for '{route_path}' not found")
    
    chain_config = settings.routes.routes[route_path]
    
    validation_result = {"valid": True, "errors": []}
    if plugin_loader:
        chain_runner = ChainRunner(plugin_loader.get_all_plugins())
        validation_result = chain_runner.validate_chain(route_path)
    
    plugin_details = []
    if plugin_loader:
        for plugin_config in chain_config.get("chain", []):
            plugin_name = plugin_config.get("plugin") if isinstance(plugin_config, dict) else plugin_config
            if plugin_name:
                metadata = plugin_loader.get_plugin_metadata(plugin_name)
                if metadata:
                    plugin_details.append({
                        "name": metadata.name, "version": metadata.version,
                        "description": metadata.description, "config": plugin_config
                    })
    
    return {
        "route": route_path, "config": chain_config, "validation": validation_result,
        "plugins": plugin_details, "total_plugins": len(plugin_details)
    }
