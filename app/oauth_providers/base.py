from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import os
import yaml

class BaseOAuthProvider(ABC):
    """
    所有 OAuth 提供商的抽象基类。
    每个提供商都必须实现这些方法以与系统兼容。
    """

    def __init__(self, provider_name: str):
        self.name = provider_name
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.redirect_uri: Optional[str] = None
        self.load_config()

    def load_config(self):
        """
        从与提供商同名的 .yml 文件加载配置。
        如果配置文件不存在，则创建一个 .yml.example 示例文件，并跳过该提供商的加载。
        """
        provider_dir = os.path.dirname(__file__)
        config_path = os.path.join(provider_dir, f"{self.name}.yml")
        example_config_path = f"{config_path}.example"

        # 如果配置文件不存在，检查并创建示例文件
        if not os.path.exists(config_path):
            if not os.path.exists(example_config_path):
                example_content = {
                    "client_id": "YOUR_CLIENT_ID_HERE",
                    "client_secret": "YOUR_CLIENT_SECRET_HERE",
                    "redirect_uri": f"http://localhost:8080/api/v1/auth/oauth/{self.name}/callback"
                }
                try:
                    with open(example_config_path, 'w', encoding='utf-8') as f:
                        yaml.dump(example_content, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                except Exception:
                    # 如果创建示例文件失败，也没必要让整个应用崩溃
                    pass
            # 抛出 FileNotFoundError，让上层调用者（OAuthProviderRegistry）知道这个 provider 未配置
            raise FileNotFoundError(f"配置文件未找到: {config_path}。已自动创建示例文件: {example_config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if not config or not isinstance(config, dict):
             raise ValueError(f"配置文件 '{config_path}' 格式无效或为空。")

        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")
        self.redirect_uri = config.get("redirect_uri")

        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError(f"配置文件 '{config_path}' 中缺少 'client_id', 'client_secret', 或 'redirect_uri'。")

    @abstractmethod
    def build_authorize_url(self, state: str, code_challenge: Optional[str] = None) -> str:
        """
        构建提供商的授权 URL 以重定向用户。

        Args:
            state: CSRF 状态令牌。
            code_challenge: PKCE 代码挑战 (可选)。

        Returns:
            完整的授权 URL。
        """
        pass

    @abstractmethod
    async def exchange_code(self, code: str, code_verifier: Optional[str]) -> Dict[str, Any]:
        """
        用授权码交换访问令牌。

        Args:
            code: 从提供商收到的授权码。
            code_verifier: PKCE 代码验证器 (可选)。

        Returns:
            包含令牌数据的字典 (例如, 'access_token')。
        """
        pass

    @abstractmethod
    async def fetch_profile(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用访问令牌从提供商获取用户个人资料。

        Args:
            token_data: exchange_code 返回的字典。

        Returns:
            包含用户个人资料信息的字典。
            必须至少包含 'id' 和 'email' (可以为 None)。
        """
        pass

    def build_pkce_challenge(self, code_verifier: str) -> str:
        """
        从验证器构建 PKCE S256 代码挑战。
        这是一个默认实现，如果需要可以覆盖。
        """
        import base64
        import hashlib
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()