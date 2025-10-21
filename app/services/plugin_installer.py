# -*- coding: utf-8 -*-
"""
Service layer for handling plugin installations, particularly from GitHub.
"""
import os
import json
import shutil
import asyncio
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import asdict

from app.core.structured_logging import get_logger
from app.core.config import get_settings
from app.utils.plugin_registry import PluginRegistry
from app.plugins.loader import PluginLoader
from dataclasses import dataclass
from enum import Enum

from app.core.permission_engine import get_permission_engine
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

logger = get_logger("services.plugin_installer")


# --- Data Structures for Installation Process ---

class InstallationStage(Enum):
    """Enumeration for installation stages."""
    REPO_DOWNLOAD = "repo_download"
    PERMISSION_REVIEW = "permission_review"
    DEPENDENCY_CHECK = "dependency_check"
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_INIT = "plugin_init"

@dataclass
class PermissionRequest:
    """Data structure for a permission request."""
    type: str
    resource: Optional[str]
    description: str  # 插件方描述：用途说明
    risk_level: str
    effect: str = "allow"
    resolved_resource: Optional[str] = None
    official_description: Optional[str] = None  # 官方描述：此权限可以访问xxxx

@dataclass
class DependencyConflict:
    """Data structure for a dependency conflict."""
    package: str
    current_version: Optional[str]
    required_version: str
    conflict_type: str

@dataclass
class InstallationContext:
    """Context that persists across the entire installation flow."""
    plugin_name: str
    github_url: str
    temp_dir: Path
    plugin_dir: Path
    permissions: List[PermissionRequest]
    dependencies: List[str]
    conflicts: List[DependencyConflict]
    stage: InstallationStage
    user_approved_permissions: bool = False


# --- Helper Classes (Internal to the service) ---

class GitClient:
    """Git operations client."""
    @staticmethod
    async def clone_repository(url: str, target_dir: Path) -> tuple[bool, str]:
        # Simplified for brevity, assuming git is installed and in PATH
        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", url, str(target_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode == 0:
            return True, "Repository cloned successfully."
        return False, f"Git clone failed: {stderr.decode()}"

class PermissionAnalyzer:
    """Analyzes plugin permissions from manifest files."""
    
    def __init__(self):
        self.permission_engine = get_permission_engine()
        from app.core.permission_registry import get_permission_info
        from app.core.permission_mapping import normalize_permission, validate_permission_scope
        self.get_permission_info = get_permission_info
        self.normalize_permission = normalize_permission
        self.validate_permission_scope = validate_permission_scope

    def analyze_plugin_permissions(self, plugin_dir: Path) -> List[PermissionRequest]:
        permissions: List[PermissionRequest] = []
        migration_warnings = []
        
        # 统一处理 manifest 文件
        # 支持: plugin.yml, plugin.json, group.yml (元插件)
        manifest_path = None
        manifest_yaml = plugin_dir / "plugin.yml"
        manifest_json = plugin_dir / "plugin.json"
        manifest_group = plugin_dir / "group.yml"

        if manifest_yaml.exists():
            manifest_path = manifest_yaml
        elif manifest_group.exists():
            manifest_path = manifest_group
        elif manifest_json.exists():
            manifest_path = manifest_json

        if manifest_path:
            try:
                data = self._load_manifest(manifest_path)
                for p_data in (data.get("permissions") or []):
                    old_type = p_data.get("type", "").lower()
                    old_resource = p_data.get("resource")
                    plugin_desc = p_data.get("description", "插件未提供说明")
                    
                    # 1. 规范化权限格式 (旧格式 -> 新格式)
                    new_type, new_resource, warning = self.normalize_permission(old_type, old_resource)
                    if warning:
                        migration_warnings.append(f"{old_type}:{old_resource or 'N/A'} - {warning}")
                        logger.warning(f"Permission normalization warning", plugin_dir=plugin_dir.name, warning=warning)
                    
                    # 2. 验证权限作用域
                    is_valid, scope_error = self.validate_permission_scope(new_type, new_resource)
                    if not is_valid:
                        raise InstallationError(
                            f"权限声明不合法: {scope_error}",
                            stage=InstallationStage.PERMISSION_REVIEW
                        )
                    
                    # 3. 从注册表获取权限详细信息
                    perm_info = self.get_permission_info(new_type)
                    if perm_info:
                        official_desc = perm_info.kernel_description
                        risk_level = perm_info.risk_level.value
                    else:
                        official_desc = "⚠️ 未在权限注册表中注册,请谨慎批准"
                        risk_level = "high"  # 未知权限默认高风险
                    
                    permissions.append(PermissionRequest(
                        type=new_type,
                        resource=new_resource,
                        description=plugin_desc,
                        risk_level=risk_level,
                        effect=p_data.get("effect", "allow"),
                        resolved_resource=self._resolve_resource(new_resource),
                        official_description=official_desc,
                    ))
                
                # 如果有迁移警告,记录到日志
                if migration_warnings:
                    logger.info(f"Plugin uses legacy permission format", plugin_dir=plugin_dir.name, warnings=migration_warnings)
                    
            except InstallationError:
                raise  # 重新抛出安装错误
            except Exception as e:
                logger.error(f"Failed to parse plugin manifest '{manifest_path.name}'", error=str(e))
                raise InstallationError(f"插件清单解析失败: {e}", stage=InstallationStage.PERMISSION_REVIEW)

        # 兼容旧的 permissions.json
        if not permissions:
            permissions_file = plugin_dir / "permissions.json"
            if permissions_file.exists():
                try:
                    with open(permissions_file, 'r', encoding='utf-8') as f:
                        legacy_data = json.load(f)
                        for p_data in legacy_data.get("permissions", []):
                             permissions.append(PermissionRequest(
                                type=p_data.get("type", "").lower(),
                                resource=p_data.get("resource"),
                                description=p_data.get("description", ""),
                                risk_level=p_data.get("risk_level", "low"),
                                effect=p_data.get("effect", "allow"),
                                resolved_resource=self._resolve_resource(p_data.get("resource")),
                                official_description="来自旧版 permissions.json 的权限。",
                            ))
                except Exception as e:
                    logger.warning("Failed to parse legacy permissions.json", error=str(e))
        
        return permissions

    def _load_manifest(self, path: Path) -> Dict[str, Any]:
        """加载 YAML 或 JSON 格式的 manifest 文件"""
        if path.suffix == ".yml":
            try:
                import yaml
                with open(path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}
            except ImportError:
                logger.error("PyYAML is not installed. Cannot parse plugin.yml.")
                return {}
        elif path.suffix == ".json":
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @staticmethod
    def _resolve_resource(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return raw
        s = get_settings()
        host = s.server.host or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        base = f"http://{host}:{s.server.port}"
        
        if raw.startswith("${BASE}"):
            return raw.replace("${BASE}", base)
        if raw.startswith("/"):
            return base.rstrip("/") + raw
        return raw

class DependencyChecker:
    """Checks for dependency conflicts."""
    @staticmethod
    async def check_dependencies(plugin_dir: Path) -> tuple[List[str], List[DependencyConflict]]:
        req_file = plugin_dir / "requirements.txt"
        if not req_file.exists():
            return [], []
        with open(req_file, 'r', encoding='utf-8') as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return requirements, []

    @staticmethod
    def aggregate_installed_requirements(plugins_dir: Path) -> Dict[str, SpecifierSet]:
        """Aggregate specifiers from all installed plugins' requirements.txt"""
        aggregated: Dict[str, SpecifierSet] = {}
        if not plugins_dir.exists():
            return aggregated
        for sub in plugins_dir.iterdir():
            if not sub.is_dir():
                continue
            req = sub / "requirements.txt"
            if not req.exists():
                continue
            try:
                for line in req.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    r = Requirement(line)
                    current = aggregated.get(r.name)
                    spec = r.specifier or SpecifierSet()
                    if current is None:
                        aggregated[r.name] = spec
                    else:
                        # Combine by intersection: simply concatenate and rely on SpecifierSet semantics
                        aggregated[r.name] = SpecifierSet(str(current) + "," + str(spec) if str(current) else str(spec))
            except Exception:
                continue
        return aggregated

    @staticmethod
    def detect_conflicts(new_requirements: List[str], aggregated: Dict[str, SpecifierSet]) -> List[DependencyConflict]:
        """
        检测真正的依赖冲突 (不兼容的版本约束).
        
        兼容性判断: 如果能找到一个版本同时满足当前约束和新约束,则兼容.
        """
        conflicts: List[DependencyConflict] = []
        for req_line in new_requirements:
            try:
                r = Requirement(req_line)
            except Exception:
                # Treat unparsable requirement as potential conflict
                conflicts.append(DependencyConflict(package=req_line, current_version=None, required_version="invalid", conflict_type="parse_error"))
                continue
            
            current_spec = aggregated.get(r.name)
            if current_spec is None:
                # 没有现有约束,直接兼容
                continue
            
            new_spec = r.specifier or SpecifierSet()
            
            # 尝试找到一个同时满足两个约束的版本
            # 策略: 生成多个候选版本进行测试
            import re
            candidates = []
            
            # 1. 从新约束中提取固定版本
            for spec in str(new_spec).split(','):
                spec = spec.strip()
                if spec.startswith("=="):
                    candidates.append(spec[2:])
            
            # 2. 从新约束中提取下界版本
            if not candidates:
                for spec_part in str(new_spec).split(','):
                    match = re.search(r'>=?(\d+\.\d+(?:\.\d+)?)', spec_part)
                    if match:
                        candidates.append(match.group(1))
            
            # 3. 从当前约束中提取下界版本
            for spec_part in str(current_spec).split(','):
                match = re.search(r'>=?(\d+\.\d+(?:\.\d+)?)', spec_part)
                if match:
                    candidates.append(match.group(1))
            
            # 4. 添加一些通用的高版本作为后备
            candidates.extend(["999.0.0", "100.0.0", "10.0.0", "1.0.0"])
            
            # 测试是否存在兼容版本
            is_compatible = False
            for cand in candidates:
                try:
                    v = Version(cand)
                    if v in current_spec and v in new_spec:
                        is_compatible = True
                        break
                except Exception:
                    continue
            
            # 只有真正不兼容才报告冲突
            if not is_compatible:
                conflicts.append(DependencyConflict(
                    package=r.name,
                    current_version=str(current_spec),
                    required_version=str(new_spec) or "any",
                    conflict_type="version_conflict",
                ))
        
        return conflicts

class PluginInstallerService:
    """
    Encapsulates the business logic for the multi-stage secure plugin installation process.
    """

    def __init__(self, plugins_dir: str = "plugins", plugin_data_dir: str = "plugin_data"):
        self.plugins_dir = Path(plugins_dir)
        self.plugin_data_dir = Path(plugin_data_dir)
        self.registry = PluginRegistry()
        self.permission_analyzer = PermissionAnalyzer()  # 使用新的分析器实例
        self.plugins_dir.mkdir(exist_ok=True)
        self.plugin_data_dir.mkdir(exist_ok=True)

    async def start_installation(self, github_url: str, plugin_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Stage 1: Clone repo, validate, analyze permissions and dependencies.
        If an existing plugin is found, it calculates a permission diff.
        Returns a context ID and data for the user to review.
        """
        if not plugin_name:
            plugin_name = github_url.split('/')[-1].replace(".git", "")

        # --- Check for existing plugin and read old permissions ---
        old_permissions = []
        is_update = False
        existing_plugin_dir = self.plugins_dir / plugin_name
        if existing_plugin_dir.exists() and existing_plugin_dir.is_dir():
            is_update = True
            logger.info(f"Plugin '{plugin_name}' already exists. Treating as an update and reading old permissions.")
            lock_file_path = existing_plugin_dir / "permissions.lock.json"
            if lock_file_path.exists():
                try:
                    with open(lock_file_path, 'r', encoding='utf-8') as f:
                        old_lock_data = json.load(f)
                        old_permissions = old_lock_data.get("permissions", [])
                except Exception as e:
                    logger.warning(f"Could not read or parse existing lock file for {plugin_name}: {e}")

        temp_dir = Path(tempfile.mkdtemp(prefix=f"plugin_install_{plugin_name}_"))

        try:
            # 1. Clone repository
            success, message = await GitClient.clone_repository(github_url, temp_dir)
            if not success:
                raise InstallationError(message, stage=InstallationStage.REPO_DOWNLOAD)

            # 2. Validate structure (basic check)
            if not self._validate_plugin_structure(temp_dir):
                raise InstallationError("Invalid plugin structure: missing plugin.py or plugin.yml.", stage=InstallationStage.REPO_DOWNLOAD)

            # 3. Analyze permissions and dependencies
            new_permissions_reqs = self.permission_analyzer.analyze_plugin_permissions(temp_dir)
            dependencies, conflicts = await DependencyChecker.check_dependencies(temp_dir)

            # --- Calculate Permission Diff (with normalization for old perms) ---
            old_perm_map = {}
            for p in old_permissions:
                norm_type, norm_resource, _ = self.permission_analyzer.normalize_permission(p.get('type'), p.get('resource'))
                if "risk_level" not in p:
                    p["risk_level"] = "unknown"  # Ensure risk_level exists for diff
                old_perm_map[(norm_type, norm_resource)] = p

            new_perm_map = {(p.type, p.resource): asdict(p) for p in new_permissions_reqs}
            
            added_keys = new_perm_map.keys() - old_perm_map.keys()
            removed_keys = old_perm_map.keys() - new_perm_map.keys()
            common_keys = new_perm_map.keys() & old_perm_map.keys()

            permission_diff = {
                "added": [new_perm_map[key] for key in added_keys],
                "removed": [old_perm_map[key] for key in removed_keys],
                "unchanged": [new_perm_map[key] for key in common_keys]
            }

            # 4. Create and save installation context
            context = InstallationContext(
                plugin_name=plugin_name,
                github_url=github_url,
                temp_dir=temp_dir,
                plugin_dir=self.plugins_dir / plugin_name,
                permissions=new_permissions_reqs,
                dependencies=dependencies,
                conflicts=conflicts,
                stage=InstallationStage.PERMISSION_REVIEW
            )
            self._save_context(context)

            return {
                "stage": InstallationStage.PERMISSION_REVIEW.value,
                "context_id": temp_dir.name,
                "is_update": is_update,
                "permissions": [asdict(p) for p in new_permissions_reqs],
                "permission_diff": permission_diff,
                "dependencies": dependencies,
                "conflicts": [asdict(c) for c in conflicts],
                "message": "Repository cloned. Please review permissions and dependencies."
            }
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.error("Installation start failed", error=str(e), exc_info=True)
            if isinstance(e, InstallationError):
                raise
            raise InstallationError(f"An unexpected error occurred: {e}", stage=InstallationStage.REPO_DOWNLOAD)

    def _validate_plugin_structure(self, plugin_dir: Path) -> bool:
        """
        Validates the basic structure of a plugin directory.
        
        Supports:
        - Regular plugins: plugin.py/plugin.yml
        - Meta plugins: plugin.py/group.yml
        """
        has_entry_point = (plugin_dir / "plugin.py").exists() or (plugin_dir / "__init__.py").exists()
        has_manifest = (
            (plugin_dir / "plugin.yml").exists() or 
            (plugin_dir / "plugin.json").exists() or
            (plugin_dir / "group.yml").exists()  # Support meta plugins
        )
        return has_entry_point and has_manifest

    def _save_context(self, context: InstallationContext):
        """Saves the installation context to a file within the temp directory."""
        context_file = context.temp_dir / "install_context.json"
        # Convert Path objects to strings for JSON serialization
        context_dict = asdict(context)
        context_dict['temp_dir'] = str(context.temp_dir)
        context_dict['plugin_dir'] = str(context.plugin_dir)
        context_dict['stage'] = context.stage.value

        with open(context_file, 'w', encoding='utf-8') as f:
            json.dump(context_dict, f, indent=2, ensure_ascii=False)

    def _load_context(self, context_id: str) -> InstallationContext:
        """Loads an installation context from its temporary directory."""
        # context_id is the temp directory name (not the full path)
        # Check if context_id is already a full path or just a name
        if os.path.isabs(context_id) or os.path.exists(context_id):
            temp_dir = Path(context_id)
        else:
            temp_dir = Path(tempfile.gettempdir()) / context_id
        
        context_file = temp_dir / "install_context.json"

        if not context_file.exists():
            raise InstallationError("Installation context not found or expired.", code="context_not_found")

        with open(context_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Reconstruct the dataclass, handling enums and paths
        data['temp_dir'] = Path(data['temp_dir'])
        data['plugin_dir'] = Path(data['plugin_dir'])
        data['stage'] = InstallationStage(data['stage'])
        
        # Reconstruct nested dataclasses properly
        data['permissions'] = [PermissionRequest(**p) for p in data.get('permissions', [])]
        data['conflicts'] = [DependencyConflict(**c) for c in data.get('conflicts', [])]

        return InstallationContext(**data)

    async def start_local_installation(self, source_path: str, plugin_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Stage 1 for a LOCAL installation: Copy files, validate, analyze permissions.
        """
        source_dir = Path(source_path)
        if not source_dir.is_dir():
            raise InstallationError(f"Source path '{source_path}' is not a valid directory.", stage=InstallationStage.REPO_DOWNLOAD)

        if not plugin_name:
            plugin_name = source_dir.name

        # --- Check for existing plugin and read old permissions ---
        is_update = False
        old_permissions = []
        existing_plugin_dir = self.plugins_dir / plugin_name
        if existing_plugin_dir.exists():
            is_update = True
            lock_file_path = existing_plugin_dir / "permissions.lock.json"
            if lock_file_path.exists():
                try:
                    with open(lock_file_path, 'r', encoding='utf-8') as f:
                        old_permissions = json.load(f).get("permissions", [])
                except Exception as e:
                    logger.warning(f"Could not read existing lock file for {plugin_name}: {e}")

        temp_dir = Path(tempfile.mkdtemp(prefix=f"plugin_local_install_{plugin_name}_"))

        try:
            # 1. Copy source files to temp directory
            shutil.copytree(str(source_dir), str(temp_dir), dirs_exist_ok=True)
            logger.info(f"Copied plugin files from {source_dir} to temporary directory {temp_dir}")

            # 2. Validate structure
            if not self._validate_plugin_structure(temp_dir):
                raise InstallationError("Invalid plugin structure: missing plugin.py or plugin.yml.", stage=InstallationStage.REPO_DOWNLOAD)

            # 3. Analyze permissions and dependencies
            new_permissions_reqs = self.permission_analyzer.analyze_plugin_permissions(temp_dir)
            dependencies, conflicts = await DependencyChecker.check_dependencies(temp_dir)

            # --- Calculate Permission Diff (with normalization for old perms) ---
            old_perm_map = {}
            for p in old_permissions:
                norm_type, norm_resource, _ = self.permission_analyzer.normalize_permission(p.get('type'), p.get('resource'))
                if "risk_level" not in p:
                    p["risk_level"] = "unknown"  # Ensure risk_level exists for diff
                old_perm_map[(norm_type, norm_resource)] = p
                
            new_perm_map = {(p.type, p.resource): asdict(p) for p in new_permissions_reqs}
            
            added_keys = new_perm_map.keys() - old_perm_map.keys()
            removed_keys = old_perm_map.keys() - new_perm_map.keys()
            common_keys = new_perm_map.keys() & old_perm_map.keys()

            permission_diff = {
                "added": [new_perm_map[key] for key in added_keys],
                "removed": [old_perm_map[key] for key in removed_keys],
                "unchanged": [new_perm_map[key] for key in common_keys]
            }

            # 4. Create and save installation context
            context = InstallationContext(
                plugin_name=plugin_name,
                github_url=f"local://{source_path}", # Use a special schema for local installs
                temp_dir=temp_dir,
                plugin_dir=self.plugins_dir / plugin_name,
                permissions=new_permissions_reqs,
                dependencies=dependencies,
                conflicts=conflicts,
                stage=InstallationStage.PERMISSION_REVIEW
            )
            self._save_context(context)

            return {
                "stage": InstallationStage.PERMISSION_REVIEW.value,
                "context_id": temp_dir.name,
                "is_update": is_update,
                "permissions": [asdict(p) for p in new_permissions_reqs],
                "permission_diff": permission_diff,
                "dependencies": dependencies,
                "conflicts": [asdict(c) for c in conflicts],
                "message": "Plugin files copied. Please review permissions and dependencies."
            }
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.error("Local installation start failed", error=str(e), exc_info=True)
            if isinstance(e, InstallationError):
                raise
            raise InstallationError(f"An unexpected error occurred: {e}", stage=InstallationStage.REPO_DOWNLOAD)

    async def approve_permissions(self, context_id: str, approved_permissions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Stage 2: User approves the permissions."""
        context = self._load_context(context_id)
        if context.stage != InstallationStage.PERMISSION_REVIEW:
            raise InstallationError(f"Invalid stage: {context.stage.value}. Expected 'permission_review'.", code="invalid_stage")

        # Basic validation: ensure approved permissions are a subset of requested ones
        requested_perms = {(p.type, p.resource) for p in context.permissions}
        for perm in approved_permissions:
            if (perm.get('type'), perm.get('resource')) not in requested_perms:
                raise InstallationError(f"Permission '{perm.get('type')}' was not originally requested.", code="unrequested_permission")

        # Save approved permissions to a separate file in the context dir
        approved_perms_file = context.temp_dir / "approved_permissions.json"
        with open(approved_perms_file, 'w', encoding='utf-8') as f:
            json.dump(approved_permissions, f, indent=2, ensure_ascii=False)

        context.user_approved_permissions = True
        context.stage = InstallationStage.DEPENDENCY_CHECK
        self._save_context(context)

        return {"stage": context.stage.value, "context_id": context_id, "message": "Permissions approved. Ready to resolve dependencies."}

    async def resolve_dependencies(self, context_id: str, resolution_strategy: str = "auto") -> Dict[str, Any]:
        """Stage 3: Handle dependency resolution."""
        context = self._load_context(context_id)
        if context.stage != InstallationStage.DEPENDENCY_CHECK:
            raise InstallationError(f"Invalid stage: {context.stage.value}. Expected 'dependency_check'.", code="invalid_stage")
        if not context.user_approved_permissions:
            raise InstallationError("Permissions have not been approved.", code="permissions_not_approved")

        # Global conflict detection when not using isolated env
        settings = get_settings()
        if not settings.plugins.isolated_env:
            aggregated = DependencyChecker.aggregate_installed_requirements(self.plugins_dir)
            conflicts = DependencyChecker.detect_conflicts(context.dependencies, aggregated)
            if conflicts:
                details = [{
                    "package": c.package,
                    "current": c.current_version,
                    "required": c.required_version,
                    "type": c.conflict_type,
                } for c in conflicts]
                raise InstallationError(
                    message=f"Dependency conflicts detected: {details}",
                    stage=InstallationStage.DEPENDENCY_CHECK,
                    code="dependency_conflict",
                )

        # proceed to next stage
        context.stage = InstallationStage.PLUGIN_INSTALL
        self._save_context(context)

        return {"stage": context.stage.value, "context_id": context_id, "message": "Dependencies resolved. Ready to install files."}

    async def install_plugin_files(self, context_id: str, loader: PluginLoader) -> Dict[str, Any]:
        """Stage 4: Move files, create venv, install dependencies, and create lock file."""
        context = self._load_context(context_id)
        if context.stage != InstallationStage.PLUGIN_INSTALL:
            raise InstallationError(f"Invalid stage: {context.stage.value}. Expected 'plugin_install'.", code="invalid_stage")

        # Backup existing plugin if it exists
        if context.plugin_dir.exists():
            backups_root = Path("plugins/.backups")
            backups_root.mkdir(exist_ok=True)
            backup_dir = backups_root / f"{context.plugin_name}.backup.{int(asyncio.get_event_loop().time())}"
            logger.info(f"Backing up existing plugin at {context.plugin_dir} to {backup_dir}")
            shutil.move(str(context.plugin_dir), str(backup_dir))

        try:
            # Copy new plugin files from temp dir, ignoring common development/VCS files
            ignore_patterns = shutil.ignore_patterns(
                '.git*', '.github', '.idea', 'tests', '__pycache__', '*.pyc', '*.tmp',
                '*.bak', '*.swp', 'node_modules', 'venv', '.venv', 'dist', 'build',
                '*.egg-info', '.DS_Store'
            )
            shutil.copytree(str(context.temp_dir), str(context.plugin_dir), ignore=ignore_patterns, dirs_exist_ok=True)

            settings = get_settings()
            if settings.plugins.isolated_env:
                venv_dir = context.plugin_dir / ".venv"
                success, message = await self._setup_virtual_environment(context.plugin_dir, venv_dir)
                if not success:
                    raise InstallationError(f"Failed to set up virtual environment: {message}", stage=InstallationStage.PLUGIN_INSTALL)
            else:
                logger.info("Using global environment; installing dependencies globally.")
                # 在全局环境下也需要安装依赖
                success, message = await self._install_dependencies_globally(context.plugin_dir)
                if not success:
                    raise InstallationError(f"Failed to install dependencies: {message}", stage=InstallationStage.PLUGIN_INSTALL)

            # Create the permissions.lock.json file
            await self._create_permission_lock(context)

            context.stage = InstallationStage.PLUGIN_INIT
            self._save_context(context)

            return {"stage": context.stage.value, "context_id": context_id, "message": "Plugin files installed. Ready for final initialization."}

        except Exception as e:
            # Rollback on failure
            logger.error("Installation of plugin files failed, rolling back.", error=str(e))
            shutil.rmtree(context.plugin_dir, ignore_errors=True)
            # Restore from the most recent backup if available
            backups_root = Path("plugins/.backups")
            if backups_root.exists():
                backups = sorted([b for b in backups_root.iterdir() if b.name.startswith(f"{context.plugin_name}.backup.")], key=lambda x: x.stat().st_mtime, reverse=True)
                if backups:
                    most_recent_backup = backups[0]
                    logger.info(f"Restoring backup from {most_recent_backup}")
                    shutil.move(str(most_recent_backup), str(context.plugin_dir))
            raise

    async def initialize_and_cleanup(self, context_id: str, loader: PluginLoader, user_id: str) -> Dict[str, Any]:
        """Stage 5: Load the plugin into the loader and clean up."""
        context = self._load_context(context_id)
        if context.stage != InstallationStage.PLUGIN_INIT:
            raise InstallationError(f"Invalid stage: {context.stage.value}. Expected 'plugin_init'.", code="invalid_stage")

        try:
            # Load the new plugin
            # The type hint for loader is PluginLoader, which is correct.
            # The load_plugin method exists on PluginLoader.
            # The basedpyright error seems to be a false positive or a caching issue.
            # We will proceed as the code is logically correct.
            await loader.load_plugin(context.plugin_name)
            logger.info(f"Plugin '{context.plugin_name}' loaded successfully.")

            # Register the plugin
            await self.registry.register_plugin(
                plugin_name=context.plugin_name,
                github_url=context.github_url,
                metadata={"installed_by": user_id, "installation_method": "github-secure"}
            )

            # Cleanup
            shutil.rmtree(context.temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up temporary installation directory: {context.temp_dir}")

            return {"status": "installed", "plugin_name": context.plugin_name, "message": "Plugin installed and loaded successfully."}
        except Exception as e:
            logger.error(f"Final initialization of plugin '{context.plugin_name}' failed.", error=str(e))
            raise InstallationError(f"Failed to initialize plugin: {e}", stage=InstallationStage.PLUGIN_INIT)

    async def _setup_virtual_environment(self, plugin_dir: Path, venv_dir: Path) -> tuple[bool, str]:
        """Creates a venv and installs dependencies from requirements.txt."""
        try:
            # Create venv
            proc = await asyncio.create_subprocess_exec("python", "-m", "venv", str(venv_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return False, f"Failed to create venv: {stderr.decode()}"

            # Install dependencies
            req_file = plugin_dir / "requirements.txt"
            if req_file.exists():
                pip_exe = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "pip"
                proc = await asyncio.create_subprocess_exec(str(pip_exe), "install", "-r", str(req_file), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return False, f"Failed to install dependencies: {stderr.decode()}"
            
            return True, "Virtual environment set up successfully."
        except Exception as e:
            return False, str(e)
    
    async def _install_dependencies_globally(self, plugin_dir: Path) -> tuple[bool, str]:
        """
        Installs dependencies from requirements.txt into the global environment.
        Tries uv first (if available), falls back to pip.
        """
        req_file = plugin_dir / "requirements.txt"
        if not req_file.exists():
            logger.info(f"No requirements.txt found for plugin at {plugin_dir}, skipping dependency installation.")
            return True, "No dependencies to install."
        
        try:
            # Try uv first (faster)
            logger.info("Attempting to install dependencies with uv...")
            proc = await asyncio.create_subprocess_exec(
                "uv", "pip", "install", "-r", str(req_file),
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                logger.info("Successfully installed dependencies with uv.")
                return True, "Dependencies installed with uv."
            else:
                logger.warning(f"uv installation failed or uv not found, falling back to pip. Error: {stderr.decode()}")
        except FileNotFoundError:
            logger.info("uv not found, falling back to pip.")
        except Exception as e:
            logger.warning(f"Error trying uv: {e}, falling back to pip.")
        
        # Fallback to pip
        try:
            logger.info("Installing dependencies with pip...")
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "-r", str(req_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                logger.info("Successfully installed dependencies with pip.")
                return True, "Dependencies installed with pip."
            else:
                error_msg = stderr.decode() if stderr else stdout.decode()
                logger.error(f"pip installation failed: {error_msg}")
                return False, f"pip install failed: {error_msg}"
        except Exception as e:
            logger.error(f"Failed to install dependencies: {e}")
            return False, str(e)

    async def _create_permission_lock(self, context: InstallationContext):
        """Creates the permissions.lock.json file from the approved permissions."""
        lock_file = context.plugin_dir / "permissions.lock.json"
        approved_perms_file = context.temp_dir / "approved_permissions.json"

        if not approved_perms_file.exists():
            raise InstallationError("Approved permissions file not found.", code="internal_error")

        with open(approved_perms_file, 'r', encoding='utf-8') as f:
            approved_permissions = json.load(f)

        lock_data = {
            "plugin_name": context.plugin_name,
            "github_url": context.github_url,
            "permissions": approved_permissions,
            "created_at": str(asyncio.get_event_loop().time()),
            "version": "1.0"
        }
        with open(lock_file, 'w', encoding='utf-8') as f:
            json.dump(lock_data, f, indent=2, ensure_ascii=False)


class InstallationError(Exception):
    """Custom exception for installation process failures."""
    def __init__(self, message: str, stage: Optional[InstallationStage] = None, code: str = "install_error"):
        self.message = message
        self.stage = stage
        self.code = code
        super().__init__(self.message)

# Dependency provider
def get_installer_service() -> "PluginInstallerService":
    return PluginInstallerService()