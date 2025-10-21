import click
import requests
import sys
from pathlib import Path
import json
import os
from urllib.parse import urljoin
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.prompt import Confirm
from rich import box
from urllib.parse import urlparse

# Add project root to path to import permission registry
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.core.permission_registry import get_permission_info, RiskLevel

# --- Configuration ---
CONFIG_DIR = Path.home() / ".prism"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_API_URL = "http://127.0.0.1:8080/api/v1/"

console = Console()

# --- Helper Functions ---

def ensure_config_dir():
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(exist_ok=True)
    # 设置目录权限为仅用户可读写执行 (700)
    if os.name != 'nt': # Windows 不支持 chmod
        os.chmod(CONFIG_DIR, 0o700)

def save_tokens(access_token: str, refresh_token: str):
    """保存认证令牌"""
    ensure_config_dir()
    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(credentials, f)
    # 设置文件权限为仅用户可读写 (600)
    if os.name != 'nt':
        os.chmod(CREDENTIALS_FILE, 0o600)

def load_tokens() -> dict:
    """加载认证令牌"""
    if not CREDENTIALS_FILE.exists():
        return {}
    with open(CREDENTIALS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def clear_tokens():
    """清除已保存的令牌"""
    if CREDENTIALS_FILE.exists():
        os.remove(CREDENTIALS_FILE)

def get_api_url() -> str:
    """获取 API 地址"""
    # 环境变量优先
    env_url = os.environ.get("PRISM_API_URL")
    if env_url:
        return env_url
    if not CONFIG_FILE.exists():
        return DEFAULT_API_URL
    with open(CONFIG_FILE, "r") as f:
        try:
            config = json.load(f)
            return config.get("api_url", DEFAULT_API_URL)
        except json.JSONDecodeError:
            return DEFAULT_API_URL


# --- API Client ---

class ApiClient:
    """
    用于与 Prism 后端 API 交互的客户端。
    自动处理认证、令牌刷新和错误。
    """
    def __init__(self):
        self.base_url = get_api_url()
        self.tokens = load_tokens()
        self.session = requests.Session()
        self._update_headers()

    def _root_base(self) -> str:
        """返回根级别 Base URL（去掉 /api/v1 前缀）"""
        u = self.base_url
        if "/api/v1/" in u:
            root = u.split("/api/v1/")[0] + "/"
        elif u.rstrip("/").endswith("/api/v1"):
            root = u[: -len("/api/v1")] + "/"
        else:
            root = u if u.endswith("/") else (u + "/")
        return root

    def _update_headers(self):
        """使用当前 access_token 更新请求头"""
        access_token = self.tokens.get("access_token")
        if access_token:
            self.session.headers.update({"Authorization": f"Bearer {access_token}"})
        else:
            self.session.headers.pop("Authorization", None)
            
    def _refresh_token(self) -> bool:
        """使用 refresh_token 获取新的令牌"""
        refresh_token = self.tokens.get("refresh_token")
        if not refresh_token:
            console.print("[bold red]错误: 需要刷新令牌，但未找到。请重新登录。[/bold red]")
            return False

        console.print("[yellow]会话已过期，正在尝试刷新...[/yellow]")
        try:
            response = self.session.post(
                urljoin(self.base_url, "identity/refresh"),
                json={"refresh_token": refresh_token}
            )
            response.raise_for_status()
            
            new_tokens = response.json()
            self.tokens = {
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens["refresh_token"],
            }
            save_tokens(self.tokens["access_token"], self.tokens["refresh_token"])
            self._update_headers()
            console.print("[green]会话刷新成功！[/green]")
            return True
            
        except requests.exceptions.HTTPError as e:
            error_data = e.response.json() if e.response else {}
            detail = error_data.get("detail", e.response.text if e.response else "未知错误")
            console.print(f"[bold red]刷新令牌失败: {detail} ({e.response.status_code})。请重新登录。[/bold red]")
            clear_tokens()
            self.tokens = {}
            self._update_headers()
            return False
        except requests.exceptions.RequestException as e:
            console.print(f"[bold red]网络错误，刷新令牌失败: {e}[/bold red]")
            return False

    def request(self, method, endpoint, **kwargs):
        """
        发送一个请求，并自动处理令牌刷新。
        """
        url = urljoin(self.base_url, endpoint)
        try:
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 401:
                if self._refresh_token():
                    # 刷新成功后重试一次请求
                    response = self.session.request(method, url, **kwargs)
            
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            error_data = e.response.json() if e.response and 'application/json' in e.response.headers.get('Content-Type','') else {}
            detail = error_data.get("detail", e.response.text if e.response else "未知错误")
            console.print(Panel(f"[bold]请求失败: {detail}[/bold]\n[dim]URL: {method.upper()} {url}\n状态码: {e.response.status_code}[/dim]", 
                            title="[bold red]API 错误[/bold red]", border_style="red"))
            sys.exit(1)

    def request_root(self, method, endpoint, **kwargs):
        """
        使用根 Base URL（无 /api/v1）发送请求，用于管理员端点 /admin/...。
        """
        url = urljoin(self._root_base(), endpoint)
        try:
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 401:
                if self._refresh_token():
                    response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_data = e.response.json() if e.response and 'application/json' in e.response.headers.get('Content-Type','') else {}
            detail = error_data.get("detail", e.response.text if e.response else "未知错误")
            console.print(Panel(f"[bold]请求失败: {detail}[/bold]\n[dim]URL: {method.upper()} {url}\n状态码: {e.response.status_code}[/dim]",
                            title="[bold red]API 错误[/bold red]", border_style="red"))
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            console.print(Panel(f"[bold]无法连接到 Prism 服务器: {e}[/bold]\n[dim]请检查服务器是否正在运行，以及 Base URL ({self._root_base()}) 是否正确。[/dim]",
                            title="[bold red]网络错误[/bold red]", border_style="red"))
            sys.exit(1)


@click.group()
def cli():
    """
    Prism 管理员命令行工具。
    
    在使用大部分命令前，请先使用 `login` 命令登录。
    """
    pass

@cli.command()
@click.option('--username', prompt="管理员用户名", help="您的管理员用户名。")
@click.option('--password', prompt="密码", hide_input=True, help="您的密码。")
def login(username, password):
    """登录到 Prism 服务器并保存会话。"""
    api_url = get_api_url()
    console.print(f"正在尝试登录到 [cyan]{api_url}[/cyan] ...")
    
    try:
        response = requests.post(
            urljoin(api_url, "identity/login"),
            json={"username": username, "password": password}
        )
        response.raise_for_status()
        
        data = response.json()
        save_tokens(data["access_token"], data["refresh_token"])
        console.print("[bold green]✔ 登录成功！[/bold green] 会话已保存。")

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        if status_code == 401 or status_code == 400:
             detail = e.response.json().get("detail", "用户名或密码错误。")
             console.print(f"[bold red]登录失败: {detail}[/bold red]")
        else:
             console.print(f"[bold red]登录时发生未知错误: {e.response.text} (状态码: {status_code})[/bold red]")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]网络错误，无法连接到服务器: {e}[/bold red]")
        sys.exit(1)

@cli.command()
def logout():
    """登出并清除本地保存的会话。"""
    # 也可以在这里调用后端的 /logout 端点，但这对于CLI不是必须的
    clear_tokens()
    console.print("[bold green]已成功登出。[/bold green]")

def _is_github_url(source: str) -> bool:
    """判断输入是否为 GitHub URL"""
    return source.startswith(('http://', 'https://', 'git@', 'git://'))

def _resolve_local_path(source: str) -> str:
    """解析本地路径:支持插件名、相对路径、绝对路径"""
    from pathlib import Path
    
    # 1. 如果是绝对路径且存在
    if Path(source).is_absolute() and Path(source).exists():
        return str(Path(source).resolve())
    
    # 2. 如果是相对路径且存在
    if Path(source).exists():
        return str(Path(source).resolve())
    
    # 3. 尝试作为插件名,在 plugins/ 目录下查找
    plugin_dir = Path("plugins") / source
    if plugin_dir.exists():
        return str(plugin_dir.resolve())
    
    # 4. 如果都不存在,返回原始路径(让后续错误处理)
    return str(Path(source).resolve())

@cli.command()
@click.argument('source')
@click.option('--yes', is_flag=True, help='自动批准权限，无需交互确认')
def install(source, yes):
    """
    安装或更新一个插件(自动识别 GitHub URL 或本地路径)。
    
    SOURCE: 
        - GitHub URL: https://github.com/user/repo.git
        - 插件名: oauth_key_frontend (从 plugins/ 目录)
        - 相对路径: ./my_plugin 或 plugins/my_plugin
        - 绝对路径: G:\\Prism\\plugins\\my_plugin
    """
    client = ApiClient()
    
    # 智能识别安装方式
    if _is_github_url(source):
        console.print(f"[dim]检测到 GitHub URL,使用远程安装模式[/dim]")
        install_endpoint = "admin/plugins/install/start"
        payload = {"github_url": source}
    else:
        console.print(f"[dim]检测到本地路径,使用本地安装模式[/dim]")
        local_path = _resolve_local_path(source)
        console.print(f"[dim]解析路径: {local_path}[/dim]")
        install_endpoint = "admin/plugins/install/from-local"
        payload = {"source_path": local_path}
    
    # 1. 启动安装流程
    console.print(f"正在启动安装...")
    start_data = client.request_root("post", install_endpoint, json=payload)
    
    context_id = start_data["data"]["context_id"]
    is_update = start_data["data"]["is_update"]
    diff = start_data["data"]["permission_diff"]
    
    op_type = "更新" if is_update else "安装"
    console.print(f"[green]✔ 已成功启动插件 {op_type} 流程。上下文ID: {context_id}[/green]")
    
    # 2. 显示权限变更并请求管理员批准
    console.print(f"\n[bold yellow]🔐 插件权限变更审查 ({op_type})[/bold yellow]")
    
    has_changes = False
    for p in diff.get("added", []):
        has_changes = True
        perm_type = p.get('type', '')
        perm_resource = p.get('resource', '')
        
        # 从权限注册表获取详细信息
        perm_info = get_permission_info(perm_type)
        
        # 确定风险颜色
        if perm_info:
            risk_level = perm_info.risk_level.value
            tier = perm_info.tier.value
        else:
            risk_level = p.get('risk_level', 'low')
            tier = 'unknown'
        
        risk_color = {
            'critical': 'red bold',
            'high': 'red',
            'medium': 'yellow',
            'low': 'green'
        }.get(risk_level, 'white')
        
        tier_color = 'red' if tier == 'admin' else 'blue'
        tier_badge = f"[{tier_color}][{tier.upper()}][/{tier_color}]"
        
        console.print(f"\n[bold green]+ 新增权限[/bold green] {tier_badge}")
        console.print(f"  [bold]权限名称[/bold]: [{risk_color}]{perm_type}[/{risk_color}]")
        
        if perm_resource:
            console.print(f"  [bold]资源限定[/bold]: {perm_resource}")
        
        # 显示内核说明
        if perm_info:
            console.print(f"  [blue]内核说明[/blue]: {perm_info.kernel_description}")
            console.print(f"  [dim]作用域限制[/dim]: {perm_info.scope_limit}")
        else:
            console.print(f"  [blue]内核说明[/blue]: [dim](未注册的权限,请谨慎批准)[/dim]")
        
        # 显示插件提供的理由
        plugin_desc = p.get('description', '插件未提供用途说明')
        console.print(f"  [cyan]插件说明[/cyan]: {plugin_desc}")
        
        console.print(f"  [bold]风险等级[/bold]: [{risk_color}]{risk_level.upper()}[/{risk_color}]")
    
    for p in diff.get("removed", []):
        has_changes = True
        console.print(f"\n[bold red]- 移除权限[/bold red]:")
        console.print(f"  [bold]权限内容[/bold]: {p.get('type', '').upper()}.{p.get('resource', 'N/A')}")
        console.print(f"  [dim]描述[/dim]: {p.get('description', 'N/A')}")
    
    if diff.get("unchanged"):
        console.print(f"\n[dim]保持不变的权限 ({len(diff.get('unchanged', []))}):[/dim]")
        for p in diff.get("unchanged", []):
            console.print(f"  [dim]{p.get('type', '').upper()}.{p.get('resource', 'N/A')}[/dim]")

    if not has_changes:
        console.print("[yellow]插件权限未发生任何变化。[/yellow]")

    if not yes and not Confirm.ask(f"[bold yellow]你是否批准以上权限变更并继续 {op_type}？[/bold yellow]"):
        console.print(f"[red]{op_type} 已取消。[/red]")
        sys.exit(0)

    # 在这个设计中，我们批准所有新声明的权限
    approved_permissions = diff.get("added", []) + diff.get("unchanged", [])

    # 3. 批准权限
    console.print("正在提交已批准的权限...")
    client.request_root("post", "admin/plugins/install/approve", json={
        "context_id": context_id,
        "approved_permissions": approved_permissions
    })
    console.print("[green]✔ 权限已批准。[/green]")

    # 4. 解析并安装依赖
    console.print("正在解析并安装依赖...")
    client.request_root("post", "admin/plugins/install/resolve", json={"context_id": context_id})
    console.print("[green]✔ 依赖已处理。[/green]")
    
    # 5. 安装插件文件
    console.print("正在安装插件文件...")
    client.request_root("post", "admin/plugins/install/files", json={"context_id": context_id})
    console.print("[green]✔ 插件文件已安装。[/green]")

    # 6. 完成安装
    console.print("正在完成安装并加载插件...")
    finish_data = client.request_root("post", "admin/plugins/install/finish", json={"context_id": context_id})
    plugin_name = finish_data["data"]["plugin_name"]
    console.print(Panel(f"[bold green]🎉 插件 '{plugin_name}' 已成功 {op_type}！[/bold green]",
                    title="[bold green]操作完成[/bold green]", border_style="green"))


@cli.command(name="install-local")
@click.argument('path')
@click.option('--yes', is_flag=True, help='自动批准权限，无需交互确认')
def install_local(path, yes):
    """
    从本地目录安装一个插件。

    PATH: 插件本地目录路径 (例如 G:\\Prism\\plugins\\my_plugin)
    """
    client = ApiClient()

    source_path = str(Path(path).resolve())
    if not Path(source_path).exists():
        console.print(f"[bold red]路径不存在: {source_path}[/bold red]")
        sys.exit(1)

    console.print(f"正在从本地目录 [cyan]{source_path}[/cyan] 启动安装...")
    start_data = client.request_root("post", "admin/plugins/install/from-local", json={"source_path": source_path})

    context_id = start_data["data"]["context_id"]
    diff = start_data["data"]["permission_diff"]

    # 权限显示与审批
    console.print(f"\n[bold yellow]🔐 插件权限变更审查 (本地安装)[/bold yellow]")
    
    has_changes = False
    for p in diff.get("added", []):
        has_changes = True
        risk_color = "red" if p.get("risk_level") == "high" else "yellow" if p.get("risk_level") == "medium" else "green"
        
        console.print(f"\n[bold green]+ 新增权限[/bold green]:")
        console.print(f"  [bold]权限内容[/bold]: [{risk_color}]{p.get('type', '').upper()}.{p.get('resource', 'N/A')}[/{risk_color}]")
        
        official_desc = p.get('official_description', '系统未提供官方描述')
        console.print(f"  [blue]权限介绍[/blue]: {official_desc}")
        
        plugin_desc = p.get('description', '插件未提供用途说明')
        console.print(f"  [cyan]权限用途[/cyan]: {plugin_desc}")
        
        console.print(f"  [bold]风险等级[/bold]: [{risk_color}]{p.get('risk_level', 'low')}[/{risk_color}]")
        
        resolved = p.get('resolved_resource')
        if resolved and resolved != p.get('resource'):
            console.print(f"  [dim]解析后资源[/dim]: {resolved}")
    
    if not has_changes:
        console.print("[yellow]该插件没有声明任何权限。[/yellow]")
        approved_permissions = []
    else:
        if not yes and not Confirm.ask(f"[bold yellow]你是否批准以上权限并继续安装？[/bold yellow]"):
            console.print(f"[red]安装已取消。[/red]")
            sys.exit(0)
        approved_permissions = diff.get("added", []) + diff.get("unchanged", [])

    # 提交审批、安装依赖、复制文件、完成
    client.request_root("post", "admin/plugins/install/approve", json={
        "context_id": context_id,
        "approved_permissions": approved_permissions
    })
    console.print("[green]✔ 权限已批准。[/green]")

    client.request_root("post", "admin/plugins/install/resolve", json={"context_id": context_id})
    console.print("[green]✔ 依赖已处理。[/green]")

    client.request_root("post", "admin/plugins/install/files", json={"context_id": context_id})
    console.print("[green]✔ 插件文件已安装。[/green]")

    finish_data = client.request_root("post", "admin/plugins/install/finish", json={"context_id": context_id})
    plugin_name = finish_data["data"]["plugin_name"]
    console.print(Panel(f"[bold green]🎉 插件 '{plugin_name}' 已成功 安装！[/bold green]",
                    title="[bold green]操作完成[/bold green]", border_style="green"))


@cli.command(name="routes")
@click.argument('plugin_name', required=False)
def list_routes(plugin_name: str | None):
    """
    列出已挂载的插件路由。

    - 不带参数：列出所有插件路由
    - 带 plugin_name：仅列出指定插件的路由
    """
    client = ApiClient()
    
    # 1. 获取已加载的插件列表
    try:
        plugins_data = client.request_root("get", "admin/plugins/")
        loaded_plugins = {p['name']: p for p in plugins_data.get('data', [])}
    except Exception as e:
        console.print(Panel(f"[bold red]无法获取插件列表: {e}[/bold red]", title="[bold red]错误[/bold red]", border_style="red"))
        sys.exit(1)
    
    if not loaded_plugins:
        console.print("[yellow]没有已加载的插件。[/yellow]")
        return
    
    # 2. 获取 OpenAPI 文档
    openapi_url = urljoin(client._root_base(), "openapi.json")
    try:
        resp = client.session.get(openapi_url)
        resp.raise_for_status()
        spec = resp.json()
    except Exception as e:
        console.print(Panel(f"[bold red]无法获取 OpenAPI 文档: {e}[/bold red]\n[dim]URL: {openapi_url}[/dim]", title="[bold red]错误[/bold red]", border_style="red"))
        sys.exit(1)

    # 3. 查找插件路由 (实际挂载在 /plugins/<plugin_name>/*)
    paths: dict = spec.get("paths", {})
    rows = []
    
    for path, methods in paths.items():
        # 只查找 /plugins/ 开头的路由
        if not path.startswith("/plugins/"):
            continue
        
        # 如果指定了插件名,过滤
        if plugin_name and not path.startswith(f"/plugins/{plugin_name}/"):
            continue
        
        for method, meta in (methods or {}).items():
            method_upper = method.upper()
            if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                continue
            summary = meta.get("summary") or meta.get("operationId") or ""
            # 提取插件名
            path_parts = path.split('/')
            plugin_in_path = path_parts[2] if len(path_parts) > 2 else "unknown"
            rows.append((plugin_in_path, method_upper, path, summary))

    if not rows:
        target = plugin_name or "所有插件"
        console.print(f"[yellow]未找到与 {target} 相关的已挂载路由。[/yellow]")
        console.print(f"[dim]已加载插件: {', '.join(loaded_plugins.keys())}[/dim]")
        console.print(f"[dim]提示:插件可能未定义任何路由,或路由注册失败。[/dim]")
        return

    table = Table(title="已挂载插件路由", box=box.SIMPLE_HEAVY)
    table.add_column("PLUGIN", style="yellow", no_wrap=True)
    table.add_column("METHOD", style="cyan", no_wrap=True)
    table.add_column("PATH", style="green")
    table.add_column("SUMMARY", style="magenta")
    for plugin, method, path, summary in sorted(rows, key=lambda x: (x[0], x[2], x[1])):
        table.add_row(plugin, method, path, summary)
    console.print(table)


@cli.command(name="update")
@click.argument('source')
@click.option('--yes', is_flag=True, help='自动批准权限，无需交互确认')
def update_plugin(source, yes):
    """
    更新插件(自动识别 GitHub URL 或本地路径)。
    
    SOURCE: GitHub URL、插件名、相对路径或绝对路径
    """
    # 更新和安装使用同一个流程,直接复用 install 逻辑
    install.callback(source, yes)


@cli.command(name="list")
def list_plugins():
    """
    列出所有已安装和已注册的插件及其状态。
    元插件会显示其子插件的树状结构。
    """
    client = ApiClient()
    console.print("正在获取插件列表...")
    
    data = client.request_root("get", "admin/plugins/registry")
    plugins = data.get("data", [])
    
    if not plugins:
        console.print("[yellow]未找到任何插件。[/yellow]")
        return
        
    table = Table(title="插件状态", box=box.SIMPLE_HEAVY)
    table.add_column("插件名称", style="cyan", no_wrap=True)
    table.add_column("注册状态", style="magenta")
    table.add_column("安装状态", style="green")
    table.add_column("来源 (Git URL)", style="yellow", no_wrap=False)

    for plugin in sorted(plugins, key=lambda p: p['name']):
        reg_status = plugin.get('registry_status', 'N/A')
        inst_status = plugin.get('installation_status', 'N/A')
        
        reg_style = "green" if reg_status == "registered" else "red"
        inst_style = "green" if inst_status == "installed" else "yellow" if inst_status == "not_found" else "red"
        
        # 主插件行
        plugin_name = plugin['name']
        subplugins = plugin.get('subplugins', [])
        
        # 如果有子插件,显示为元插件
        if subplugins:
            plugin_name = f"📦 {plugin_name} (元插件)"
        
        table.add_row(
            plugin_name,
            f"[{reg_style}]{reg_status}[/{reg_style}]",
            f"[{inst_style}]{inst_status}[/{inst_style}]",
            plugin.get('github_url', '本地或未知')
        )
        
        # 添加子插件行
        for i, subplugin in enumerate(subplugins):
            is_last = (i == len(subplugins) - 1)
            prefix = "  └─ " if is_last else "  ├─ "
            table.add_row(
                f"[dim]{prefix}{subplugin}[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]子插件[/dim]"
            )
    
    console.print(table)


@cli.command(name="reload")
@click.argument('name')
def reload_plugin(name):
    """
    重新加载一个已安装的插件。
    
    这会卸载然后重新加载插件，通常用于开发时应用代码更改。

    NAME: 要重新加载的插件名称（目录名）
    """
    client = ApiClient()
    console.print(f"正在重新加载插件 [cyan]{name}[/cyan]...")
    
    data = client.request_root("post", f"admin/plugins/{name}/reload")
    
    console.print(Panel(f"[bold green]✔ {data.get('message', f'插件 {name} 重新加载成功。')}[/bold green]",
                    title="[bold green]重新加载完成[/bold green]", border_style="green"))


@cli.command(name="info")
@click.argument('name')
def plugin_info(name):
    """
    显示指定插件的详细信息。

    NAME: 要查看信息的插件名称
    """
    client = ApiClient()
    console.print(f"正在获取插件 [cyan]{name}[/cyan] 的详细信息...")
    
    data = client.request_root("get", f"admin/plugins/{name}")
    plugin = data.get("data")
    
    if not plugin:
        console.print(f"[red]未找到插件 '{name}'。[/red]")
        return
    
    # 创建详细信息面板
    info_text = f"""[bold]插件名称:[/bold] {plugin.get('name', 'N/A')}
[bold]版本:[/bold] {plugin.get('version', 'N/A')}
[bold]描述:[/bold] {plugin.get('description', 'N/A')}
[bold]作者:[/bold] {plugin.get('author', 'N/A')}
[bold]状态:[/bold] {plugin.get('status', 'N/A')}

[bold]依赖项:[/bold] {', '.join(plugin.get('dependencies', [])) if plugin.get('dependencies') else '无'}

[bold]权限 ({len(plugin.get('permissions', []))}):[/bold]"""
    
    for perm in plugin.get('permissions', []):
        perm_type = perm.get('type', 'unknown')
        perm_resource = perm.get('resource', '')
        perm_desc = perm.get('description', '')
        info_text += f"\n  • [cyan]{perm_type}[/cyan]"
        if perm_resource:
            info_text += f".[yellow]{perm_resource}[/yellow]"
        if perm_desc:
            info_text += f" - {perm_desc}"
    
    if plugin.get('models'):
        info_text += f"\n\n[bold]提供的模型 ({len(plugin.get('models', []))}):[/bold]"
        for model in plugin.get('models', []):
            info_text += f"\n  • [green]{model.get('id', 'unknown')}[/green] - {model.get('name', 'N/A')}"
    
    # 安全信息
    security = plugin.get('security', {})
    if security:
        violations = security.get('violations', [])
        warnings = security.get('security_warnings', [])
        
        if violations or warnings:
            info_text += f"\n\n[bold red]安全警告:[/bold red]"
            for warning in warnings:
                info_text += f"\n  ⚠️  {warning}"
            for violation in violations:
                info_text += f"\n  🚨 {violation}"
    
    console.print(Panel(info_text, title=f"[bold]插件信息: {name}[/bold]", border_style="blue"))


@cli.command(name="uninstall")
@click.argument('name')
@click.option('--yes', is_flag=True, help='跳过确认提示直接卸载')
def uninstall(name, yes):
    """
    完全卸载一个插件。

    此操作将从磁盘上删除插件文件并清除其注册信息。这是一个不可逆的操作。

    NAME: 要卸载的插件的名称（目录名）。
    """
    if not yes:
        if not Confirm.ask(f"[bold red]你确定要完全卸载插件 '{name}' 吗？\n这是一个不可逆的操作，将会删除所有相关文件。[/bold red]"):
            console.print("[yellow]卸载已取消。[/yellow]")
            sys.exit(0)
    
    client = ApiClient()
    console.print(f"正在卸载插件 [cyan]{name}[/cyan]...")
    
    # API 端点是 /admin/plugins/{plugin_name}/uninstall
    endpoint = f"admin/plugins/{name}/uninstall"
    
    data = client.request_root("delete", endpoint)
    
    console.print(Panel(f"[bold green]✔ {data.get('message', f'插件 {name} 已成功卸载。')}[/bold green]",
                    title="[bold green]卸载完成[/bold green]", border_style="green"))


@cli.command(name="load")
@click.argument('name')
def load_plugin(name):
    """
    加载已存在于 plugins 目录但未激活的插件。

    NAME: 插件名称（目录名）
    """
    client = ApiClient()
    data = client.request_root("post", "admin/plugins/load", json={"name": name})
    console.print(Panel(f"[bold green]✔ {data.get('message', f'插件 {name} 加载成功。')}[/bold green]",
                    title="[bold green]加载完成[/bold green]", border_style="green"))


@cli.command(name="update-local")
@click.argument('path')
@click.option('--yes', is_flag=True, help='自动批准权限，无需交互确认')
def update_local(path, yes):
    """
    从本地目录更新一个已安装插件。

    PATH: 插件本地目录路径 (例如 G:\\Prism\\plugins\\my_plugin)
    """
    # 更新和安装使用同一个流程,直接复用 install_local 逻辑
    install_local.callback(path, yes)

    source_path = str(Path(path).resolve())
    if not Path(source_path).exists():
        console.print(f"[bold red]路径不存在: {source_path}[/bold red]")
        sys.exit(1)

    console.print(f"正在从本地目录 [cyan]{source_path}[/cyan] 启动更新...")
    start_data = client.request_root("post", "admin/plugins/install/from-local", json={"source_path": source_path})

    context_id = start_data["data"]["context_id"]
    diff = start_data["data"]["permission_diff"]

    # 权限显示与审批
    console.print(f"\n[bold yellow]🔐 插件权限变更审查 (本地更新)[/bold yellow]")

    has_changes = False
    for p in diff.get("added", []):
        has_changes = True
        risk_color = "red" if p.get("risk_level") == "high" else "yellow" if p.get("risk_level") == "medium" else "green"
        console.print(f"\n[bold green]+ 新增权限[/bold green]:")
        console.print(f"  [bold]权限内容[/bold]: [{risk_color}]{p.get('type', '').upper()}.{p.get('resource', 'N/A')}[/{risk_color}]")
        console.print(f"  [blue]权限介绍[/blue]: {p.get('official_description', '系统未提供官方描述')}")
        console.print(f"  [cyan]权限用途[/cyan]: {p.get('description', '插件未提供用途说明')}")
        console.print(f"  [bold]风险等级[/bold]: [{risk_color}]{p.get('risk_level', 'low')}[/{risk_color}]")

    if not has_changes:
        console.print("[yellow]插件权限未发生任何变化。[/yellow]")

    if not yes and not Confirm.ask("[bold yellow]你是否批准以上权限并继续更新？[/bold yellow]"):
        console.print("[red]更新已取消。[/red]")
        sys.exit(0)

    approved_permissions = diff.get("added", []) + diff.get("unchanged", [])

    client.request_root("post", "admin/plugins/install/approve", json={
        "context_id": context_id,
        "approved_permissions": approved_permissions
    })
    console.print("[green]✔ 权限已批准。[/green]")

    client.request_root("post", "admin/plugins/install/resolve", json={"context_id": context_id})
    console.print("[green]✔ 依赖已处理。[/green]")

    client.request_root("post", "admin/plugins/install/files", json={"context_id": context_id})
    console.print("[green]✔ 插件文件已安装。[/green]")

    finish_data = client.request_root("post", "admin/plugins/install/finish", json={"context_id": context_id})
    plugin_name = finish_data["data"]["plugin_name"]
    console.print(Panel(f"[bold green]🎉 插件 '{plugin_name}' 已成功 更新！[/bold green]",
                    title="[bold green]操作完成[/bold green]", border_style="green"))


if __name__ == '__main__':
    cli()
