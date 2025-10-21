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

# --- Configuration ---
CONFIG_DIR = Path.home() / ".prism"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1/"

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
        except requests.exceptions.RequestException as e:
            console.print(Panel(f"[bold]无法连接到 Prism 服务器: {e}[/bold]\n[dim]请检查服务器是否正在运行，以及 API URL ({self.base_url}) 是否正确。[/dim]",
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
            data={"username": username, "password": password} # 表单数据
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

@cli.command()
@click.argument('url')
def install(url):
    """
    从 Git URL 安全地安装或更新一个插件。
    
    URL: 插件的 Git 仓库地址 (例如 https://github.com/user/repo.git)
    """
    client = ApiClient()
    
    # 1. 启动安装流程
    console.print(f"正在从 [cyan]{url}[/cyan] 启动安装...")
    start_data = client.request("post", "admin/plugins/install/start", json={"git_url": url})
    
    context_id = start_data["data"]["context_id"]
    is_update = start_data["data"]["is_update"]
    diff = start_data["data"]["permission_diff"]
    
    op_type = "更新" if is_update else "安装"
    console.print(f"[green]✔ 已成功启动插件 {op_type} 流程。上下文ID: {context_id}[/green]")
    
    # 2. 显示权限变更并请求管理员批准
    if not diff["added"] and not diff["removed"] and not diff["unchanged"]:
        console.print("[yellow]该插件没有声明任何权限。[/yellow]")
        approved_permissions = []
    else:
        table = Table(title=f"插件权限变更审查 ({op_type})", border_style="yellow")
        table.add_column("状态", justify="center", style="white")
        table.add_column("权限类型", style="cyan")
        table.add_column("资源", style="magenta")
        table.add_column("描述", style="green")

        has_changes = False
        for p in diff["added"]:
            has_changes = True
            table.add_row("[bold green]+ 新增[/bold green]", p['type'], p['resource'], p.get('description', ''))
        for p in diff["removed"]:
            has_changes = True
            table.add_row("[bold red]- 移除[/bold red]", p['type'], p['resource'], p.get('description', ''))
        for p in diff["unchanged"]:
            table.add_row("[dim]  未变[/dim]", p['type'], p['resource'], p.get('description', ''))

        if not has_changes:
            console.print("[yellow]插件权限未发生任何变化。[/yellow]")
        else:
            console.print(table)
        
        if not Confirm.ask(f"[bold yellow]你是否批准以上权限变更并继续 {op_type}？[/bold yellow]"):
            console.print(f"[red]{op_type} 已取消。[/red]")
            sys.exit(0)

        # 在这个设计中，我们批准所有新声明的权限
        approved_permissions = diff["added"] + diff["unchanged"]

    # 3. 批准权限
    console.print("正在提交已批准的权限...")
    client.request("post", "admin/plugins/install/approve", json={
        "context_id": context_id,
        "approved_permissions": approved_permissions
    })
    console.print("[green]✔ 权限已批准。[/green]")

    # 4. 解析并安装依赖
    console.print("正在解析并安装依赖...")
    client.request("post", "admin/plugins/install/resolve_dependencies", json={"context_id": context_id})
    console.print("[green]✔ 依赖已处理。[/green]")
    
    # 5. 安装插件文件
    console.print("正在安装插件文件...")
    client.request("post", "admin/plugins/install/files", json={"context_id": context_id})
    console.print("[green]✔ 插件文件已安装。[/green]")

    # 6. 完成安装
    console.print("正在完成安装并加载插件...")
    finish_data = client.request("post", "admin/plugins/install/finish", json={"context_id": context_id})
    plugin_name = finish_data["data"]["plugin_name"]
    console.print(Panel(f"[bold green]🎉 插件 '{plugin_name}' 已成功 {op_type}！[/bold green]",
                    title="[bold green]操作完成[/bold green]", border_style="green"))


if __name__ == '__main__':
    cli()
