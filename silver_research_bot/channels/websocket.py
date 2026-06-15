"""WebSocket 服务器通道：silver_research_bot 充当 WebSocket 服务器，并为已连接的客户端提供服务."""

from __future__ import annotations

import asyncio
import email.utils
import hmac
import http
import json
import mimetypes
import re
import secrets
import ssl
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger
from pydantic import Field, field_validator, model_validator
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from silver_research_bot.bus.events import OutboundMessage
from silver_research_bot.bus.queue import MessageBus
from silver_research_bot.channels.base import BaseChannel
from silver_research_bot.config.schema import Base

if TYPE_CHECKING:
    from silver_research_bot.session.manager import SessionManager


def _strip_trailing_slash(path: str) -> str:
    """移除尾部斜杠，空路径变为 '/'"""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _normalize_config_path(path: str) -> str:
    """路径规范化： 确保路径一致"""
    return _strip_trailing_slash(path)


class WebSocketConfig(Base):
    """WebSocket 服务器通道配置。

    客户端通过类似 ``ws://{host}:{port}{path}?client_id=...&token=...`` 的 URL 进行连接。
    - ``client_id``：用于 ``allow_from`` 授权；若省略，将生成一个值并记录在日志中。
    - ``token``：若不为空，则 ``token`` 查询参数可与该静态密钥匹配；来自 ``token_issue_path`` 的短效令牌
      同样被接受。
    - ``token_issue_path``：若不为空，向该路径发送 **GET**（HTTP/1.1）请求将返回 JSON
      ``{“token”: “...”, “expires_in”: <seconds>}``；在打开 WebSocket 时使用 ``?token=...``。
      必须与 ``path``（WebSocket 升级路径）不同。如果客户端与
      silver_research_bot 在 **同一进程** 中运行并共享 asyncio 循环，请使用线程或异步 HTTP 客户端进行 GET 请求——切勿在协程内部调用
      阻塞的 ``urllib`` 或同步的 ``httpx``。
    - ``token_issue_secret``：若不为空，令牌请求必须发送 ``Authorization: Bearer <secret>`` 或
      ``X-silver_research_bot-Auth: <secret>``。
    - ``websocket_requires_token``：若为 True，握手必须包含有效的令牌（静态令牌或已签发且未过期的令牌）。
    - 每个连接都有独立的会话：内部通过唯一的 ``chat_id`` 映射到代理会话。
    - 出站消息中的 ``media`` 字段包含本地文件系统路径；远程客户端需要
      共享文件系统或 HTTP 文件服务器才能访问这些文件。
    """

    enabled: bool = False
    '是否启用WebSocket服务，默认关闭'
    host: str = "127.0.0.1"
    '监听的主机地址，默认本地回环地址'
    port: int = 8765
    '监听端口，默认8765'
    path: str = "/"
    'WebSocket连接路径，默认根路径'
    token: str = ""
    '静态认证令牌'
    token_issue_path: str = ""
    '临时令牌签发接口路径'
    token_issue_secret: str = ""
    '令牌签发接口的认证密钥'
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    '临时令牌有效期（秒），默认300秒，范围30-86400'
    websocket_requires_token: bool = True
    '是否要求令牌认证，默认开启'
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    '允许连接的客户端ID列表，默认允许所有'
    streaming: bool = True
    '是否启用消息流式传输，默认开启'
    max_message_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    '最大消息字节数，默认1MB，范围1KB-16MB'
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    '心跳包发送间隔（秒），默认20秒'
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    '心跳超时时间（秒），默认20秒'
    ssl_certfile: str = ""
    'SSL证书文件路径（用于wss）'
    ssl_keyfile: str = ""
    'SSL私钥文件路径（用于wss）'

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        """检查 path 是否以 / 开头。若不是，抛出 ValueError"""
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        """先 strip() 去除首尾空白。若结果为空字符串，直接返回 ""（表示未配置）。若非空，则验证必须以 / 开头，否则报错"""
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        """若配置了 token_issue_path（非空），则将其规范化后的值与规范化后的 path 比较。如果两者相同，则抛出 ValueError，要求两个路径必须不同"""
        if not self.token_issue_path:
            return self
        if _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self


def _http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    """生成标准的 Response 对象"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def _read_webui_model_name() -> str | None:
    """返回用于只读 WebUI 显示的已配置默认模型."""
    try:
        from silver_research_bot.config.loader import load_config

        model = load_config().agents.defaults.model.strip()
        return model or None
    except Exception as e:
        logger.debug("webui bootstrap could not load model name: {}", e)
        return None


def _parse_request_path(path_with_query: str) -> tuple[str, dict[str, list[str]]]:
    """将请求路径拆分为路径和查询参数"""
    parsed = urlparse("ws://x" + path_with_query)
    path = _strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query)


def _normalize_http_path(path_with_query: str) -> str:
    """返回路径组件（不含查询字符串），并将尾部斜杠标准化（根路径保持为 ``/``）."""
    return _parse_request_path(path_with_query)[0]


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    """只取查询参数"""
    return _parse_request_path(path_with_query)[1]


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    """返回 *key* 的第一个值，或 None。"""
    values = query.get(key)
    return values[0] if values else None


def _parse_inbound_payload(raw: str) -> str | None:
    """将客户端数据帧解析为文本；若内容为空或无法识别，则返回 None."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


# 接受 UUID 以及类似“unified:default”的短范围键。这样可以保持能力
# 命名空间足够小，从而杜绝路径遍历/引号注入等漏洞利用手段。
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")
'chat_id校验规则： 接受 UUID 以及类似“unified:default”的短范围键'

def _is_valid_chat_id(value: Any) -> bool:
    """校验chat_id"""
    return isinstance(value, str) and _CHAT_ID_RE.match(value) is not None


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """如果帧是新式 JSON 封装，则返回一个带类型的封装字典；否则返回 None。

    当帧解析为包含字符串 ``type`` 字段的 JSON 对象时，即符合条件。
    旧版帧（纯文本，或不包含 ``type`` 的 ``{“content”: ...}`` 格式）将返回 None；
    调用方应针对此类情况回退到 :func:`_parse_inbound_payload`。
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
'localhost地址'

# 匹配旧版 chat-id 模式，但同时也支持文件系统安全的根路径，
# 因此该 API 可以处理那些键值来自非 WebSocket 通道的会话。
_API_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
'API_KEY校验规则：'

def _decode_api_key(raw_key: str) -> str | None:
    """解码一个百分比编码的 API 路径片段，然后验证结果。"""
    key = unquote(raw_key)
    if _API_KEY_RE.match(key) is None:
        return None
    return key


def _is_localhost(connection: Any) -> bool:
    """判断连接是否来自环回地址（用于 WebUI bootstrap 的安全性）"""
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    # ``::ffff:127.0.0.1`` is loopback in IPv6-mapped form.
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in _LOCALHOSTS


def _http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    """生成标准的 Response 对象"""
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def _http_error(status: int, message: str | None = None) -> Response:
    """生成标准的 Response 对象"""
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return _http_response(body, status=status)


def _bearer_token(headers: Any) -> str | None:
    """从请求头提取 Bearer token"""
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """检测实际的 Web 服务升级；对同一路径的普通 HTTP GET 请求应被忽略."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    """如果 token-issue HTTP 请求携带的凭据与 ``token_issue_secret`` 匹配，则返回 True。"""
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-silver_research_bot-Auth") or headers.get("x-silver_research_bot-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)


class WebSocketChannel(BaseChannel):
    """运行一个本地 WebSocket 服务器；将 text/JSON 消息转发至消息总线。"""

    name = "websocket"
    '通道唯一标识名称'
    display_name = "WebSocket"
    '界面显示的通道名称'

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        session_manager: "SessionManager | None" = None,
        static_dist_path: Path | None = None,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        '如果配置是字典格式，将其验证并转换为WebSocketConfig对象'
        super().__init__(config, bus)
        '调用父类初始化方法'
        self.config: WebSocketConfig = config
        'WebSocket配置实例'
        self._subs: dict[str, set[Any]] = {}
        '会话ID映射到订阅该会话的连接集合，用于消息广播'
        self._conn_chats: dict[Any, set[str]] = {}
        '连接映射到其订阅的会话ID集合，断开连接时快速清理'
        self._conn_default: dict[Any, str] = {}
        '连接对应的默认会话ID，兼容无路由的旧版帧'
        self._issued_tokens: dict[str, float] = {}
        '一次性令牌，在WebSocket握手时消耗'
        self._api_tokens: dict[str, float] = {}
        '多用途API令牌，用于WebUI接口，仅验证不消耗'
        self._stop_event: asyncio.Event | None = None
        '服务停止事件'
        self._server_task: asyncio.Task[None] | None = None
        'WebSocket服务器异步任务'
        self._session_manager = session_manager
        '会话管理器实例'
        self._static_dist_path: Path | None = (static_dist_path.resolve() if static_dist_path is not None else None)
        '静态文件分发目录路径'

    # -------------------------------- 订阅管理-------------------------------------------

    def _attach(self, connection: Any, chat_id: str) -> None:
        """将连接加入 _subs[chat_id] 集合，同时记录 _conn_chats[connection].add(chat_id)。操作是幂等的"""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _cleanup_connection(self, connection: Any) -> None:
        """从每个订阅集移除 *connection*；可安全地多次调用。"""
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
        self._conn_default.pop(connection, None)

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """向单个连接发送一个控制事件（已连接、错误等）。"""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            logger.warning("websocket: failed to send {} event: {}", event, e)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "websocket: ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    # -------------------------------- 令牌管理 -------------------------------------------
    _MAX_ISSUED_TOKENS = 10_000

    def _purge_expired_issued_tokens(self) -> None:
        """遍历 _issued_tokens，删除过期的令牌"""
        now = time.monotonic()
        for token_key, expiry in list(self._issued_tokens.items()):
            if now > expiry:
                self._issued_tokens.pop(token_key, None)

    def _take_issued_token_if_valid(self, token_value: str | None) -> bool:
        """验证并使用一个已签发的令牌（每次连接尝试仅限使用一次）。

        使用单步弹出操作，以最大限度缩短查找与移除之间的间隔；
        在 asyncio 的单线程协作模型下是安全的。
        """
        if not token_value:
            return False
        self._purge_expired_issued_tokens()
        expiry = self._issued_tokens.pop(token_value, None)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            return False
        return True

    def _handle_token_issue_http(self, connection: Any, request: Any) -> Any:
        """处理 token_issue_path 请求。
        检查 token_issue_secret 凭证（如果配置了），
        然后生成一个新令牌（格式 nbwt_<random>），
        将其存入 _issued_tokens 并返回 JSON {"token": "...", "expires_in": seconds}"""
        secret = self.config.token_issue_secret.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            logger.warning(
                "websocket: token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        self._purge_expired_issued_tokens()
        if len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS:
            logger.error(
                "websocket: too many outstanding issued tokens ({}), rejecting issuance",
                len(self._issued_tokens),
            )
            return _http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        self._issued_tokens[token_value] = time.monotonic() + float(self.config.token_ttl_s)

        return _http_json_response(
            {"token": token_value, "expires_in": self.config.token_ttl_s}
        )

    # ----------------- HTTP 请求分发 ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """将传入的 HTTP 请求路由到处理程序或 WS 升级路径。"""
        got, query = _parse_request_path(request.path)

        # 1. Token issue endpoint (legacy, optional, gated by configured secret).
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue_http(connection, request)

        # 2. WebUI bootstrap: localhost-only, mints tokens for the embedded UI.
        if got == "/webui/bootstrap":
            return self._handle_webui_bootstrap(connection)

        # 3. REST surface for the embedded UI.
        if got == "/api/sessions":
            return self._handle_sessions_list(request)

        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            return self._handle_session_messages(request, m.group(1))

        # NOTE: websockets' HTTP parser only accepts GET, so we cannot expose a
        # true ``DELETE`` verb. The action is folded into the path instead.
        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        # 4. WebSocket upgrade (the channel's primary purpose). Only run the
        # handshake gate on requests that actually ask to upgrade; otherwise
        # a bare ``GET /`` from the browser would be rejected as an
        # unauthorized WS handshake instead of serving the SPA's index.html.
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # 5. Static SPA serving (only if a build directory was wired in).
        if self._static_dist_path is not None:
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    # ----------------------- HTTP 路由处理 ------------------------------------------------

    def _check_api_token(self, request: WsRequest) -> bool:
        """根据 API 令牌池（可重复使用，受 TTL 限制）验证请求。"""
        self._purge_expired_api_tokens()
        token = _bearer_token(request.headers) or _query_first(
            _parse_query(request.path), "token"
        )
        if not token:
            return False
        expiry = self._api_tokens.get(token)
        if expiry is None or time.monotonic() > expiry:
            self._api_tokens.pop(token, None)
            return False
        return True

    def _purge_expired_api_tokens(self) -> None:
        """验证 Authorization: Bearer <token> 或查询参数 token 是否存在于 _api_tokens 且未过期"""
        now = time.monotonic()
        for token_key, expiry in list(self._api_tokens.items()):
            if now > expiry:
                self._api_tokens.pop(token_key, None)

    def _handle_webui_bootstrap(self, connection: Any) -> Response:
        """
        仅在本地回环地址可访问
        它同时将令牌加入 _issued_tokens 和 _api_tokens，并返回令牌、WebSocket 路径、默认模型名等信息。
        这样前端可以用同一个令牌既完成 WebSocket 握手（消耗一份），又用于后续 REST API 调用（另一份）

        """
        if not _is_localhost(connection):
            return _http_error(403, "webui bootstrap is localhost-only")
        # Cap outstanding tokens to avoid runaway growth from a misbehaving client.
        self._purge_expired_issued_tokens()
        self._purge_expired_api_tokens()
        if (
            len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS
            or len(self._api_tokens) >= self._MAX_ISSUED_TOKENS
        ):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
            )
        token = f"nbwt_{secrets.token_urlsafe(32)}"
        expiry = time.monotonic() + float(self.config.token_ttl_s)
        # Same string registered in both pools: the WS handshake consumes one copy
        # while the REST surface keeps validating the other until TTL expiry.
        self._issued_tokens[token] = expiry
        self._api_tokens[token] = expiry
        return _http_json_response(
            {
                "token": token,
                "ws_path": self._expected_path(),
                "expires_in": self.config.token_ttl_s,
                "model_name": _read_webui_model_name(),
            }
        )

    def _handle_sessions_list(self, request: WsRequest) -> Response:
        """列出所有以 "websocket:" 开头的会话（过滤掉其他通道的会话），返回 JSON"""
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        sessions = self._session_manager.list_sessions()
        # The webui is only meaningful for websocket-channel chats — CLI /
        # Slack / Lark / Discord sessions can't be resumed from the browser,
        # so leaking them into the sidebar is just noise. Filter to the
        # ``websocket:`` prefix and strip absolute paths on the way out.
        cleaned = [
            {k: v for k, v in s.items() if k != "path"}
            for s in sessions
            if isinstance(s.get("key"), str) and s["key"].startswith("websocket:")
        ]
        return _http_json_response({"sessions": cleaned})

    @staticmethod
    def _is_webui_session_key(key: str) -> bool:
        """如果 *key* 属于 WebUI 的 WebSocket 专用接口，则返回 True。"""
        return key.startswith("websocket:")

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        """读取某个会话的历史消息文件（JSONL 格式），返回完整内容"""
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # The embedded webui only understands websocket-channel sessions. Keep
        # its read surface aligned with ``/api/sessions`` instead of letting a
        # caller probe arbitrary CLI / Slack / Lark history by handcrafted URL.
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        data = self._session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        return _http_json_response(data)

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        """删除会话文件"""
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # Same boundary as ``_handle_session_messages``: the webui may only
        # mutate websocket sessions, and deletion really does unlink the local
        # JSONL, so keep the blast radius narrow and explicit.
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        deleted = self._session_manager.delete_session(decoded_key)
        return _http_json_response({"deleted": bool(deleted)})

    def _serve_static(self, request_path: str) -> Response | None:
        """将 *request_path* 映射到构建好的 SPA 目录；若未找到，则回退到 index.html。"""
        assert self._static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # Reject path-traversal attempts and absolute targets.
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self._static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self._static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            # SPA history-mode fallback: unknown routes serve index.html so the
            # client-side router can render them.
            index = self._static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError as e:
            logger.warning("websocket static: failed to read {}: {}", candidate, e)
            return _http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        # Hash-named build assets are cache-friendly; index.html must stay fresh.
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache)],
        )

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        """WebSocket 握手授权"""
        supplied = _query_first(query, "token")
        static_token = self.config.token.strip()

        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                return None
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if self.config.websocket_requires_token:
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            self._take_issued_token_if_valid(supplied)
        return None

    async def start(self) -> None:
        """服务器启动"""
        '''1.设置 _running 和 _stop_event'''
        self._running = True
        self._stop_event = asyncio.Event()

        '''2.根据 SSL 配置构建 ssl.SSLContext（如果提供了证书和私钥）'''
        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        '''3.定义 process_request 回调（即 _dispatch_http）和 WebSocket 消息处理器 handler（即 _connection_loop）'''
        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        logger.info(
            "WebSocket server listening on {}://{}:{}{}",
            scheme,
            self.config.host,
            self.config.port,
            self.config.path,
        )
        if self.config.token_issue_path:
            logger.info(
                "WebSocket token issue route: {}://{}:{}{}",
                scheme,
                self.config.host,
                self.config.port,
                _normalize_config_path(self.config.token_issue_path),
            )

        '''4.调用 websockets.serve() 启动服务器，并等待停止事件'''
        async def runner() -> None:
            async with serve(
                handler,
                self.config.host,
                self.config.port,
                process_request=process_request,
                max_size=self.config.max_message_bytes,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=self.config.ping_timeout_s,
                ssl=ssl_context,
            ):
                assert self._stop_event is not None
                await self._stop_event.wait()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        """每个 WebSocket 连接的入口"""
        '''1.从查询参数中提取 client_id，若未提供则生成一个 anon-xxx 格式的 ID，并截断过长的值'''
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            logger.warning("websocket: client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        '''2.生成一个默认的 chat_id（UUID）'''
        default_chat_id = str(uuid.uuid4())

        '''3.立即向客户端发送 ready 事件，包含 chat_id 和 client_id'''
        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                    },
                    ensure_ascii=False,
                )
            )

            '''4.注册该连接：记录 _conn_default[connection] = default_chat_id，并调用 _attach(connection, default_chat_id)'''
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._attach(connection, default_chat_id)

            '''
            5.循环接收消息帧：
                -解码二进制帧为 UTF-8 字符串。
                -尝试解析为信封（_parse_envelope）：如果成功，调用 _dispatch_envelope 处理高级操作（创建新会话、切换会话、发送消息）。
                -否则按普通文本消息处理：提取内容（_parse_inbound_payload），调用 _handle_message 发送到总线，使用默认的 chat_id。
                '''
            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning("websocket: ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                )

        except Exception as e:
            logger.debug("websocket connection ended: {}", e)
        finally:
            self._cleanup_connection(connection)

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """
        信封消息处理
        支持三种信封类型：
        new_chat：生成新的 UUID 作为 chat_id，将当前连接订阅到该新会话，并回复 attached 事件。
        attach：将当前连接订阅到指定的已有 chat_id（需通过 _is_valid_chat_id 格式检查），回复 attached。
        message：将当前连接自动订阅到指定的 chat_id（若尚未订阅），然后调用 _handle_message 将内容发送到总线。
        """
        t = envelope.get("type")
        if t == "new_chat":
            new_id = str(uuid.uuid4())
            self._attach(connection, new_id)
            await self._send_event(connection, "attached", chat_id=new_id)
            return
        if t == "attach":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            await self._send_event(connection, "attached", chat_id=cid)
            return
        if t == "message":
            cid = envelope.get("chat_id")
            content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(content, str) or not content.strip():
                await self._send_event(connection, "error", detail="missing content")
                return
            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                metadata={"remote": getattr(connection, "remote_address", None)},
            )
            return
        await self._send_event(connection, "error", detail=f"unknown type: {t!r}")

    async def stop(self) -> None:
        """停止服务"""
        '''1.设置 _running = False'''
        if not self._running:
            return
        self._running = False

        '''2.设置 _stop_event，让服务器任务退出 async with serve... 上下文'''
        if self._stop_event:
            self._stop_event.set()

        '''3.等待 _server_task 完成'''
        if self._server_task:
            try:
                await self._server_task
            except Exception as e:
                logger.warning("websocket: server task error during shutdown: {}", e)
            self._server_task = None

        '''4.清空所有订阅和令牌池'''
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        self._issued_tokens.clear()
        self._api_tokens.clear()

    async def _safe_send_to(self, connection: Any, raw: str, *, label: str = "") -> None:
        """将原始帧发送到一个连接，并在 ConnectionClosed 时进行清理。"""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
            logger.warning("websocket{}connection gone", label)
        except Exception as e:
            logger.error("websocket{}send failed: {}", label, e)
            raise

    async def send(self, msg: OutboundMessage) -> None:
        """出站消息发送"""
        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            logger.warning("websocket: no active subscribers for chat_id={}", msg.chat_id)
            return
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": msg.content,
        }
        if msg.media:
            payload["media"] = msg.media
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        raw = json.dumps(payload, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        meta = metadata or {}
        if meta.get("_stream_end"):
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")
