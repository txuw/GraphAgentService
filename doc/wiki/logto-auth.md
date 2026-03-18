# Logto JWT 鉴权链路

本文档说明当前项目中 Logto 鉴权的接入方式、请求生命周期，以及后续扩展时应该遵守的边界。

当前实现目标很明确：

- 保护 `/api/*` 业务接口
- 校验来自 Logto 的 Bearer JWT
- 将 JWT 中的 `sub` 解析为 `user_id`
- 将用户身份放入请求上下文，供路由与后续依赖读取

当前版本只做“认证”，不做 scope / RBAC 授权喵～

## 1. 适用场景

这里采用的是典型的 Resource Server / API Protection 方案，而不是“服务端自己发起登录、维护 session”的传统 Web 登录方案。

也就是说，当前链路假设：

1. 前端或其他客户端先向 Logto 获取 access token
2. 客户端调用 OverMindAgent API 时携带 `Authorization: Bearer <token>`
3. OverMindAgent 服务端只负责验证 token 与提取用户身份

因此这条链路的核心是：

```text
Bearer JWT
-> JWK 公钥解析
-> JWT claims 校验
-> claims.sub
-> AuthenticatedUser.user_id
```

## 2. 配置结构

当前配置位于 `settings.yaml`：

```yaml
logto:
  enabled: true
  issuer_uri: https://login.txuw.top/oidc
  audience: https://api.txuw.top
  jwk_set_uri: https://login.txuw.top/oidc/jwks
```

字段含义：

- `enabled`：是否启用 Logto 鉴权
- `issuer_uri`：JWT 的 `iss` 预期值
- `audience`：JWT 的 `aud` 预期值
- `jwk_set_uri`：JWKS 公钥地址，用于验签

`common/config.py` 仍然只负责加载与合并配置，不承担鉴权业务判断。

## 3. 代码结构

当前鉴权代码集中在 `src/overmindagent/common/auth/`：

```text
common/auth/
├── __init__.py
├── errors.py
├── models.py
└── service.py
```

职责约定如下：

- `models.py`：定义 `AuthenticatedUser`
- `errors.py`：定义统一的鉴权异常 `AuthenticationError`
- `service.py`：实现 `LogtoAuthenticator`
- `__init__.py`：统一导出对外入口

API 层相关接入点：

- `src/overmindagent/main.py`
- `src/overmindagent/api/router.py`
- `src/overmindagent/api/dependencies.py`

## 4. 应用生命周期

### 应用启动阶段

在 `create_app()` 中会创建一次 `LogtoAuthenticator`，并挂到 `app.state`：

```python
logto_authenticator = LogtoAuthenticator(settings.get("logto", {}))
app.state.logto_authenticator = logto_authenticator
```

这表示：

- `LogtoAuthenticator` 是应用级单例
- 它的配置在应用启动时确定
- 它内部持有的 `PyJWKClient` 也随应用实例一起存活

当前没有额外的后台刷新线程，也没有服务端 session 存储。

### 应用运行阶段

所有 `/api/*` 请求都会通过统一的 FastAPI dependency 进入鉴权逻辑。

公开接口目前不走这条链路：

- `/`
- `/hello/{name}`
- `/health`

## 5. 请求生命周期

一次受保护请求的流转大致如下：

```text
HTTP 请求进入
-> FastAPI 命中 /api 路由
-> 执行 require_current_user()
-> 取出 app.state.logto_authenticator
-> 解析 Authorization: Bearer <token>
-> 通过 jwk_set_uri 获取签名公钥
-> 校验签名 / iss / aud / exp
-> claims.sub -> AuthenticatedUser.user_id
-> 写入 request.state.current_user
-> 路由函数继续执行
```

### 第 1 步：路由统一挂鉴权依赖

`api/router.py` 中对 `/api` 下的 `chat_router` 和 `graphs_router` 统一挂了：

```python
dependencies=[Depends(require_current_user)]
```

因此只要进入这些路由，鉴权一定先执行。

### 第 2 步：依赖层把鉴权异常转成 HTTP 语义

`require_current_user()` 做两件事：

- 调用 `authenticator.authenticate_request(request)`
- 将 `AuthenticationError` 转成 `401 Unauthorized`

这样鉴权错误不会泄漏底层 JWT 库异常。

### 第 3 步：请求级缓存用户对象

`authenticate_request()` 会先检查：

```python
request.state.current_user
```

如果当前请求已经解析过用户，就直接复用，不重复验 token。

这意味着：

- 同一个请求内多次读取当前用户，不会重复解析 JWT
- 请求结束后缓存自动失效
- 不会跨请求保存身份信息

### 第 4 步：Bearer Token 解析

当前从请求头中读取：

```text
Authorization: Bearer <token>
```

如果没有头或格式错误，会直接返回 `401`。

### 第 5 步：JWT 校验

当前使用 `PyJWT + PyJWKClient` 完成验证：

1. 通过 `jwk_set_uri` 找到签名公钥
2. 使用 `RS256` 验签
3. 校验：
   - `iss == issuer_uri`
   - `aud == audience`
   - `exp` 有效

当前没有做：

- scope 校验
- organization / tenant 校验
- 自定义 claim 权限判断

### 第 6 步：用户对象构造

JWT 校验通过后，会将 claims 中的 `sub` 转换成：

```python
AuthenticatedUser(
    user_id=sub,
    subject=sub,
    claims=claims,
    is_authenticated=True,
)
```

然后写入：

```python
request.state.current_user
```

因此当前 `user_id` 的来源就是 JWT 的 `sub`。

## 6. 关闭鉴权时的行为

当 `logto.enabled=false` 时：

- `/api/*` 仍然会执行 `require_current_user()`
- 但 authenticator 不会要求 `Authorization`
- 它会返回一个匿名用户：

```python
AuthenticatedUser.anonymous()
```

匿名用户的特征：

- `user_id=None`
- `subject=None`
- `claims={}`
- `is_authenticated=False`

这样做的好处是，业务代码始终可以读取统一的用户对象，而不用在“开关打开”和“开关关闭”之间切换两套分支。

## 7. 错误语义

当前统一返回 `401 Unauthorized` 的情况包括：

- 缺少 `Authorization` 头
- `Authorization` 不是 `Bearer <token>` 格式
- JWT 已过期
- `iss` 不匹配
- `aud` 不匹配
- JWT 签名无效
- 无法从 token 中解析出合法 `sub`

当前文案策略是：

- 对调用方给出简短稳定的错误消息
- 不暴露底层堆栈或 JWKS/JWT 库细节

## 8. 当前可读取用户信息的位置

### `request.state.current_user`

最底层的请求上下文缓存，适合：

- 路由层
- 其他依赖
- 后续中间件或审计逻辑

### `get_current_user()`

这是 API dependency 层提供的便捷入口：

- 如果当前请求已经有 `request.state.current_user`，直接返回
- 否则返回匿名用户

适合在路由函数中显式声明：

```python
current_user: AuthenticatedUser = Depends(get_current_user)
```

## 9. 当前边界与限制

当前版本故意保持简单，边界如下：

- 只保护 `/api/*`
- 只做认证，不做授权
- 不把 `user_id` 自动注入 graph payload
- 不把 `user_id` 注入 `GraphRunContext`
- 不维护服务端 session
- 不处理 token 刷新

这套设计的目的，是先把“统一身份解析入口”稳定下来。

如果后续要继续扩展，建议优先按下面顺序推进：

1. 将 `AuthenticatedUser` 透传到 service 层
2. 将 `user_id` 注入 graph runtime context
3. 按接口声明 scope 并做授权校验
4. 引入审计日志与用户级 tracing

## 10. 与官方 Python SDK 的关系

当前实现没有直接使用 Logto 的官方 Python SDK，不代表 SDK 不可用，而是因为当前项目更符合“API 资源服务端”的模式。

这里更合适的做法是：

- 客户端负责拿 token
- API 服务负责验 token

如果未来项目要做：

- 服务端发起登录跳转
- OAuth/OIDC 回调处理
- Session 管理
- 传统 Web 应用登录态

再引入官方 Python SDK 会更合适。

## 11. 测试覆盖

当前测试重点覆盖了这些场景：

- 缺少 `Authorization` 头时返回 `401`
- 合法 JWT 能通过校验并解析 `user_id`
- `issuer` 不匹配时返回 `401`
- `audience` 不匹配时返回 `401`
- `enabled=false` 时返回匿名用户

测试文件位于：

- `tests/test_logto_auth.py`

## 12. 小结

当前 Logto 链路可以概括为一句话：

```text
应用启动时创建单例 authenticator
-> /api 请求进入时统一执行 dependency
-> 每次请求无状态校验 JWT
-> 将 sub 映射为 user_id
-> 写入 request.state.current_user
```

这条链路的核心价值不是“做复杂权限系统”，而是先把：

- 认证入口统一
- 用户身份解析统一
- API 层接入方式统一

这三件事稳定下来。
