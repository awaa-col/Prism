from fastapi import APIRouter

from . import api_keys, credentials, stats, plugins, chains, system, rbac, plugin_permissions

router = APIRouter()

router.include_router(api_keys.router, prefix="/api-keys", tags=["Admin - API Keys"])
router.include_router(credentials.router, prefix="/credentials", tags=["Admin - Credentials"])
router.include_router(stats.router, prefix="/stats", tags=["Admin - Statistics"])
router.include_router(plugins.router, prefix="/plugins", tags=["Admin - Plugins"])
router.include_router(chains.router, prefix="/chains", tags=["Admin - Chains"])
router.include_router(system.router, prefix="/system", tags=["Admin - System"])
router.include_router(rbac.router, prefix="/rbac", tags=["Admin - RBAC"])
router.include_router(plugin_permissions.router, prefix="/plugin-permissions", tags=["Admin - Plugin Permissions"])