from __future__ import annotations
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import httpx

from app.oauth_providers.base import BaseOAuthProvider
from app.core.structured_logging import get_logger

logger = get_logger(__name__)

class GitHubOAuthProvider(BaseOAuthProvider):
    """
    GitHub OAuth 提供商实现。
    """

    # __init__ 和 name 属性现在由 BaseOAuthProvider 处理。

    def build_authorize_url(self, state: str, code_challenge: Optional[str] = None) -> str:
        """构建 GitHub 授权 URL。"""
        if not self.client_id:
            raise ValueError("GitHub OAuth 'client_id' 未配置。")
        
        params = {
            "client_id": self.client_id,
            "state": state,
            "scope": "user:email",
        }
        if code_challenge:
            params.update({
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            })
        
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: Optional[str]) -> Dict[str, Any]:
        """用授权码交换访问令牌。"""
        if not self.client_id or not self.client_secret:
            raise ValueError("GitHub OAuth 'client_id' 或 'client_secret' 未配置。")

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://github.com/login/oauth/access_token",
                    data=data,
                    headers=headers,
                )
                response.raise_for_status()
                token_data = response.json()

                # 检查 GitHub 是否在成功的响应中返回了错误
                if "error" in token_data:
                    error_details = {
                        "error": token_data.get("error"),
                        "error_description": token_data.get("error_description"),
                        "error_uri": token_data.get("error_uri"),
                    }
                    logger.error(
                        "GitHub returned an error during token exchange.",
                        **error_details
                    )
                    # 抛出明确的错误，而不是让它在 fetch_profile 中失败
                    raise ValueError(f"GitHub OAuth error: {token_data.get('error_description')}")

                return token_data
            except httpx.HTTPStatusError as e:
                logger.error(
                    "GitHub API error during token exchange",
                    status_code=e.response.status_code,
                    response_body=e.response.text,
                )
                raise ValueError(f"GitHub API error: {e.response.text}")
            except Exception as e:
                logger.error("Unexpected error during token exchange", error=str(e))
                raise

    async def fetch_profile(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """获取用户 GitHub 个人资料和主邮箱。"""
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("令牌数据中缺少 'access_token'。")

        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            try:
                # 获取用户个人资料
                profile_resp = await client.get("https://api.github.com/user", headers=headers)
                profile_resp.raise_for_status()
                profile = profile_resp.json()

                # 获取用户邮箱列表
                emails_resp = await client.get("https://api.github.com/user/emails", headers=headers)
                emails_resp.raise_for_status()
                emails = emails_resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    "GitHub API error during profile fetch",
                    status_code=e.response.status_code,
                    response_body=e.response.text,
                )
                raise ValueError(f"GitHub API error: {e.response.text}")
            except Exception as e:
                logger.error("Unexpected error during profile fetch", error=str(e))
                raise

            # 寻找主邮箱
            primary_email = next((e["email"] for e in emails if e["primary"]), None)
            
            # 如果没有主邮箱，使用公开邮箱（如果有）
            if not primary_email:
                primary_email = profile.get("email")

            # 如果还没有，使用任意一个已验证的邮箱
            if not primary_email:
                verified_email = next((e["email"] for e in emails if e["verified"]), None)
                primary_email = verified_email

            # 组合最终的个人资料
            # 确保返回的 'id' 是字符串，以保持一致性
            final_profile = {
                "id": str(profile["id"]),
                "login": profile.get("login"),
                "name": profile.get("name"),
                "email": primary_email,
            }
            return final_profile
