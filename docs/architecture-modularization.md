# QQ Bot Studio 模块化 / 接口化架构

> 状态：Phase 0（后端契约/资源统计）、Phase 4 第二阶段（前端 service/component 低风险提取）已实现。
> 本文描述目标架构、现有耦合、分阶段迁移方案、回滚方式和兼容边界。
> 所有阶段均**不改变**现有用户可见 API、页面 URL、实例数据、文件路径或安全策略。

## 1. 目标与非目标

### 1.1 目标

1. 让运行时、部署编排、扩展、状态存储、资源统计、AstrBot API 和 UI 组件之间通过稳定接口协作。
2. 允许同一套业务核心适配不同发行形态：
   - Linux + Docker Compose；
   - Windows + Docker Desktop/WSL2；
   - Windows Native（后续独立发行版）；
   - 测试环境中的 fake runtime / fake extension / fake stats。
3. 保持现有 `/api/*` 路由、静态页面 URL、实例目录、配置文件格式和 token 鉴权兼容。
4. 让每个模块可以独立 mock 测试，减少对 Docker、GitHub、AstrBot、真实宿主内存和 Windows API 的依赖。
5. 分阶段迁移，禁止一次性大爆炸重构。

### 1.2 非目标（第一阶段）

- 不重写部署算法，不改变 Docker Compose、镜像、端口、路径或旧实例数据。
- 不重构 `plugins/qqbot_enhancer/`（由插件任务负责）。
- 不重写前端页面，不拆 `static/console.js` / `static/app.js` / `static/assistant.html`。
- 不同步或替换 Windows 独立仓库；Windows Native 仅是目标适配方向。
- 不删除现有公共类、函数、私有兼容别名或旧 API 字段。
- 不改变安全边界：路径限制、SSRF 防护、Key 不泄漏、0600 权限、Docker 列表参数调用、
  token 鉴权和日志脱敏都必须保持。

## 2. 现状盘点

### 2.1 后端文件与职责

| 文件 | 当前职责 | 主要耦合 |
|---|---|---|
| [`qqbot_studio/server.py`](../qqbot_studio/server.py) | 本地 HTTP 服务、token、HTML 注入、所有 API 路由、错误映射、更新/实例/扩展/插件/统计入口 | 直接依赖 `StudioManager`、`DeployManager`、`DockerError`、`AstrBotAPIError`、静态文件 |
| [`qqbot_studio/deployer.py`](../qqbot_studio/deployer.py) | 部署 job、线程/持久化、Compose 流程、AstrBot persona、SnowLuma、插件配置、日志、状态、回滚 | 直接持有 `Store`、`DockerController`、AstrBot HTTP 客户端工厂，同时承担较多领域逻辑 |
| [`qqbot_studio/studio.py`](../qqbot_studio/studio.py) | 多实例目录、镜像节点、实例元数据、端口预留、状态汇总、资源统计、旧布局迁移 | 直接构造 `DeployManager`/`DockerController`；早期资源统计辅助函数曾内嵌于此 |
| [`qqbot_studio/assistant_ext.py`](../qqbot_studio/assistant_ext.py) | 可选助手 B 的 clone/update、配置读写、启动/停止/健康检查 | Git 子进程、宿主进程、AstrBot 数据目录、HTTP health |
| [`qqbot_studio/store.py`](../qqbot_studio/store.py) | config/secrets/state JSON、0600 原子写入、默认值和迁移 | 被 deployer/studio 直接创建，没有注入抽象 |
| [`qqbot_studio/dockerctl.py`](../qqbot_studio/dockerctl.py) | Docker/Compose 检测与命令、sudo 降级、容器状态、日志 | 可以直接由 `RuntimeBackend` 适配 |
| [`qqbot_studio/configgen.py`](../qqbot_studio/configgen.py) | Compose/.env/AstrBot/SnowLuma 配置生成、端口分配 | 文件生成逻辑与部署流程结合较紧 |
| [`qqbot_studio/astrbot_api.py`](../qqbot_studio/astrbot_api.py) | AstrBot 登录、persona、插件 HTTP 接口底层 | HTTP 细节与部署编排通过 client factory 组装 |
| [`qqbot_studio/launcher.py`](../qqbot_studio/launcher.py) | 命令行导入/部署/控制台启动、后台服务 | 直接创建 `StudioManager`/`DockerController` |
| [`qqbot_studio/contracts.py`](../qqbot_studio/contracts.py) | **第一阶段新增**：运行时、部署、扩展、状态、统计、配置、AstrBot、UI 契约 | 仅依赖标准库 typing/dataclasses |
| [`qqbot_studio/resource_stats.py`](../qqbot_studio/resource_stats.py) | **第一阶段新增**：目录/磁盘/宿主内存/Docker stats 解析，平台无关 | 标准库实现 `ResourceStats`，无 Docker 强依赖 |
| `qqbot_studio/static/*.js/html` | 向导、控制台、独立助手页；控制台已提取 API/插件配置 service 与实例设置组件 | 向导与独立助手页仍较大，后端 API 路径仍分布在页面脚本 |
| `qqbot_studio/static/services/*.js` | **Phase 4 新增**：共享 API 客户端、插件配置 schema/值归一化与 GET/POST 契约 | 经典脚本命名空间，无构建工具 |
| `qqbot_studio/static/components/*.js` | **Phase 4 新增**：实例设置 modal 生命周期/滚动锁/busy guard | 内容 section 仍由 console 注入 |

### 2.2 当前耦合点

1. `StudioManager` 直接构造 `DeployManager` 和 `DockerController`，测试时要整体替换 manager。
2. `server.py` 同时是路由表、HTML 文件加载器、错误映射器和可选功能聚合层。
3. `DeployManager` 同时承担 deployment job、运行时命令、AstrBot API、扩展配置、日志和健康检查。
4. `assistant_ext.py` 同时包含 Git 安装器、配置存储和进程生命周期。
5. `Store` 没有接口类型约束，调用方难以替换为 Windows 凭据存储或测试内存存储。
6. 静态前端没有组件边界；页面之间共享 token/API 约定靠字符串重复维护。
7. Windows Docker 与 Linux Docker 可复用 Python 核心，但 Windows Native 需要不同 RuntimeBackend。

### 2.3 现有兼容资产

- 旧 v1 根目录实例迁移到 `instances/bot-1`；旧 v2 实例缺元数据可从 compose/config 推断。
- 所有实例数据目录、配置文件路径、容器/网络命名规则和 0600/0700 权限。
- `/api/state`、`/api/instances`、`/api/job`、`/api/action`、`/api/persona`、`/api/logs`、
  `/api/mirrors`、`/api/assistant/*`、插件/记忆/统计/配置 API 的现有字段。
- HTML token 注入约定：`window.QB_TOKEN = "{{TOKEN}}";`。
- 插件目录和 `_conf_schema.json` 由 AstrBot 侧/插件任务维护，不在此阶段改动。

## 3. 目标接口

接口定义位于 [`qqbot_studio/contracts.py`](../qqbot_studio/contracts.py)。使用
`typing.Protocol`，现有类无需继承即可被适配；`runtime_checkable` 仅用于检查方法存在性，
静态类型检查仍建议使用显式类型注解。

### 3.1 RuntimeBackend

职责：检测、启动、停止、重启和读取服务/容器状态。Docker Compose、Windows Native 进程和测试 fake
都应只暴露这组最小能力。

```python
class RuntimeBackend(Protocol):
    def detect(self, *, force: bool = False) -> Any: ...
    def start(self) -> Any: ...
    def stop(self) -> Any: ...
    def restart(self) -> Any: ...
    def logs(self, service: str, lines: int = 200) -> str: ...
```

第一阶段映射：

- `DockerController` 已是默认实现；
- 未来 `WindowsDockerRuntime` 复用 Docker CLI，仅改变路径/权限探测；
- 未来 `WindowsNativeRuntime` 实现同一方法集，但底层改为进程、PID、日志文件和本机端口。

### 3.2 DeploymentProvider

职责：部署任务、状态、persona、容器/进程动作和日志，是 server 的唯一部署编排入口。

```python
class DeploymentProvider(Protocol):
    def get_status(self) -> dict[str, Any]: ...
    def start_deploy(self, **kwargs: Any) -> str: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def update_persona(self, prompt: str) -> str: ...
    def container_action(self, action: str) -> str: ...
    def get_logs(self, service: str, lines: int = 200) -> str: ...
```

第一阶段映射：`DeployManager` 已满足。后续可将 Compose 部署与 Native 部署分别实现，但
`server.py` 不改变调用契约。

### 3.3 ExtensionManager

职责：可选扩展的 `status / install / update` 生命周期；不负责启动助手本身，不读取 Key。

```python
class ExtensionManager(Protocol):
    def status(self) -> dict[str, Any]: ...
    def install(self) -> dict[str, Any]: ...
    def update(self) -> dict[str, Any]: ...
```

第一阶段映射：`AssistantExtensionManager` 已满足。插件（qqbot_enhancer）属于 AstrBot 插件生态，
不纳入本接口，由插件任务模块化。

### 3.4 StateProvider / ConfigStore

- `StateProvider`：部署状态读写。
- `ConfigStore`：config、secrets、state 和 secrets 初始化。

```python
class StateProvider(Protocol):
    def load_state(self) -> dict[str, Any]: ...
    def save_state(self, state: dict[str, Any]) -> None: ...

class ConfigStore(Protocol):
    def load_config(self) -> dict[str, Any]: ...
    def save_config(self, config: dict[str, Any]) -> Any: ...
    def load_secrets(self) -> dict[str, Any]: ...
    def save_secrets(self, secrets_data: dict[str, Any]) -> None: ...
    def ensure_secrets(self) -> dict[str, Any]: ...
```

第一阶段映射：`Store` 同时满足两者。后续可增加：
- `MemoryConfigStore`（测试）；
- Windows Credential Manager / DPAPI 适配器；
- 只读迁移视图（不修改旧文件）。

### 3.5 ResourceStats

职责：平台无关的目录、磁盘、宿主内存和 Docker stats 解析。第一阶段已从 `studio.py` 抽出。

```python
class ResourceStats(Protocol):
    def directory_usage(self, path: Path | str) -> tuple[int, int, list[str]]: ...
    def disk_usage(self, path: Path | str) -> dict[str, Any]: ...
    def host_memory(self) -> dict[str, Any]: ...
    def parse_docker_stats(self, stdout: str) -> dict[str, dict[str, Any]]: ...
```

实现：`DefaultResourceStatsProvider`（标准库 Linux `/proc`、Windows `GlobalMemoryStatusEx`、
`statvfs` 降级）。`StudioManager(root, resource_stats=...)` 支持注入 fake provider。
旧私有别名 `_directory_usage`、`_disk_usage`、`_host_memory_info`、`_parse_size_bytes`、
`_parse_percent` 仍保留导入，避免外部/测试代码回归。

### 3.6 AstrBotClient

```python
class AstrBotClient(Protocol):
    def wait_until_ready(self, timeout: float = 180.0, *, poll_interval: float = 2.0) -> None: ...
    def login(self, *, force: bool = False) -> str: ...
    def upsert_persona(self, persona_id: str, system_prompt: str) -> None: ...
```

`astrbot_api.AstrBotClient` 已满足。插件配置底层调用的 `_get_plugin_config_via_api` /
`_save_plugin_config_via_api` 仍位于 deployer，后续可上移为带认证的 `AstrBotConfigClient`
适配器，但必须保留真实 v4.29 HTTP 路径和 Key 脱敏。

### 3.7 UIComponentProvider / UIComponentDescriptor

```python
@dataclass(frozen=True)
class UIComponentDescriptor:
    component_id: str
    title: str
    route: str
    kind: str = "page"
    requires_token: bool = True
    api_prefixes: Sequence[str] = ()
    capabilities: Mapping[str, Any] = ()

class UIComponentProvider(Protocol):
    def ui_components(self) -> Sequence[UIComponentDescriptor]: ...
```

第一阶段只提供描述符，不改变静态页面或路由。后续前端阶段可将：
- 实例卡片、插件编辑器、记忆开关、资源统计、助手 B、使用助手拆为独立组件；
- 页面仍由本地服务托管，token 仍通过 `_serve_html` 注入；
- 组件只声明 API 前缀，不直接拼接端口或安全决策。

## 4. 运行时适配关系

### 4.1 Linux + Docker Compose（当前主线）

```text
server.py
  -> StudioManager
    -> DeployManager (DeploymentProvider)
      -> DockerController (RuntimeBackend)
      -> Store (ConfigStore + StateProvider)
      -> AstrBotClient (AstrBot API)
    -> AssistantExtensionManager (ExtensionManager)
    -> DefaultResourceStatsProvider (ResourceStats)
```

行为目标：与当前完全一致。所有配置生成、端口、compose、镜像、旧实例迁移、API 字段保持。

### 4.2 Windows + Docker Desktop/WSL2

```text
Windows launcher / PowerShell
  -> same server.py + StudioManager
    -> WindowsDockerRuntime (RuntimeBackend; later)
      -> Docker CLI / named pipe / WSL2
    -> same Store / DeployManager / AstrBotClient
```

当前 Windows 脚本已调用同一 Python 核心；容器路径、桌面权限和端口由 Windows 脚本处理。
本阶段不修改 Windows 独立仓库。后续需要一个薄 `WindowsDockerRuntime` 处理：
- `docker.exe` 路径、Docker Desktop 检测；
- 不尝试 POSIX sudo；
- 容器内 `ws://astrbot:6199/ws` 保持 Compose 服务名；
- 宿主浏览器 URL 由平台适配器提供，不在 UI 中硬编码。

### 4.3 Windows Native（后续）

```text
Windows launcher
  -> same server.py
    -> WindowsNativeDeploymentProvider
      -> NativeProcessRuntime (RuntimeBackend)
      -> ProcessConfigStore / AstrBotClient / ResourceStats
```

Native 版本不能依赖 `/proc`、`sudo`、`/etc/localtime`、Compose 服务名：
- 进程启动/停止/PID/日志改为 Windows 进程 API；
- AstrBot/SnowLuma 的 OneBot URL 改为 `ws://127.0.0.1:<port>/ws`；
- 端口分配、防火墙提示和 noVNC/WebUI URL 由平台适配器给出；
- 仍需保持旧实例/数据目录兼容，必要时只读导入。

## 5. 分阶段迁移计划与回滚

### Phase 0：契约与资源统计提取（本阶段已完成）

交付：
- [`qqbot_studio/contracts.py`](../qqbot_studio/contracts.py)
- [`qqbot_studio/resource_stats.py`](../qqbot_studio/resource_stats.py)
- `StudioManager` 支持 `resource_stats=` 注入
- 新增 [`tests/test_contracts_resource_stats.py`](../tests/test_contracts_resource_stats.py)
- 旧 `studio._directory_usage` 等私有别名保留

回滚：
- 恢复 `studio.py` 中旧辅助函数并删除新模块即可；或在同一进程中让
  `StudioManager(resource_stats=...)` 回退到 `DefaultResourceStatsProvider`。
- 不改数据/API，回滚不涉及迁移。

### Phase 1：配置与 AstrBot 适配器拆分

候选：
- `PluginConfigService`：把 deployer 中 schema 读取、值校验、本地合并、在线热重载拆出；
- `AstrBotConfigClient`：封装登录后配置读写，保留 v4.29 路径和脱敏；
- `ConfigStore` 适配器测试（内存/旧文件）。
回滚：通过 manager 内部降级到原实现；旧 `get_plugin_config`/`set_plugin_config` 入口必须保留。

### Phase 2：RuntimeBackend / DeploymentProvider 适配

- 提取 `DockerRuntimeBackend`（薄适配 `DockerController`）；
- 引入 runtime factory；Linux 默认 Docker，Windows 可选 Docker/Native；
- server 只依赖 `DeploymentProvider`，不直接认识 Docker CLI。
回滚：保持 `StudioManager.get_manager()` 旧签名，factory 默认返回现有 `DeployManager`。

### Phase 3：server 路由模块化

- 将 `/api/assistant/*`、实例、部署、插件、统计、更新拆为独立 route handlers；
- 保留统一 token、Host 校验、错误映射、静态文件加载和所有旧路径；
- 建立 AppContext/composition root。
回滚：按路由前缀开关切回 monolith；所有 URL 和响应字段不改。

### Phase 4：前端组件化（第二阶段进行中）

#### 4A 现状盘点

- `console.js`：实例列表/卡片 action、实例设置 modal、插件启停、插件参数编辑器、
  记忆模式、资源统计、更新/删除、AI 助手入口、30 分钟刷新。
- `app.js`：向导环境检查、账号/模型表单、部署轮询、完成页和镜像节点；仍为单文件。
- `assistant.html`：自包含 AI 助手 B 管理页；仍为单文件，保持 token 注入和自包含约束。

#### 4B 已提取（Phase 4 第二阶段）

1. [`static/services/api.js`](../qqbot_studio/static/services/api.js)
   - `createApiClient({ token, fetchImpl? })`；
   - 统一 `X-QB-Token`、JSON body、`ApiError`、404/405 fallback、HTTP 错误解析；
   - token 只存在闭包，不写 localStorage，不暴露 token getter。
2. [`static/services/plugin-config.js`](../qqbot_studio/static/services/plugin-config.js)
   - schema 类型/options/labels/slider/default/description/hint 归一化；
   - 敏感 Key/token/password/secret/credential 字段过滤；
   - values 按 schema 白名单过滤；
   - `load(instanceId, pluginId)` / `save(instanceId, pluginId, values)` 统一 GET/POST 路径。
3. [`static/components/instance-settings.js`](../qqbot_studio/static/components/instance-settings.js)
   - 实例设置 modal open/close/refresh、内容回调 `renderSections`；
   - `onVisibilityChange` 协调 body 滚动锁；
   - busy counter、关闭/遮罩防误关、焦点返回、aria-expanded/aria-busy；
   - 与插件参数二级 modal 保持独立。

#### 4C 加载方式与原因

- 采用**经典脚本命名空间**：
  - `window.QQBotStudio.services.api`
  - `window.QQBotStudio.services.pluginConfig`
  - `window.QQBotStudio.components.instanceSettings`
- 暂不引入打包器，也未把 `console.js` 改为 ES module：当前页面依赖 `window.QB_TOKEN`
  同步注入、经典脚本顺序和 Node `--check`；先以低风险方式提取边界，避免一次性改变加载语义。
- `/console` 新增本地静态资源路由：
  - `/services/*.js`
  - `/components/*.js`
  - 只允许 `.js`，解析后必须位于 static 目录内；无 CDN/网络依赖。
- 后续 Phase 4D 可迁移到原生 ES module + import map，并同步修改 token 初始化顺序和测试。

#### 4D 兼容与回滚

- 现有 DOM ID、ARIA、API 路径、响应字段和旧后端降级不变。
- 新增脚本都在 `console.js` 之前按固定顺序加载；组件缺失会在初始化时显式报错，静态测试保证顺序。
- 回滚：恢复上一版 `console.js` / `console.html` 并移除 `/services`、`/components`
  目录与静态路由即可；无数据/API 迁移。

#### 4E Phase 4 剩余债务

- 插件参数编辑器 DOM 渲染/搜索结果/表单控件生成仍在 `console.js`，可继续拆为
  `plugin-config-editor` 组件。
- 实例卡片、资源统计、记忆切换、更新/删除仍是 `console.js` 内部渲染函数。
- `app.js` 和 `assistant.html` 尚未提取 service/component，独立助手页仍保持自包含。
- 尚未引入原生 ES module/import map；待组件 API 稳定后再迁移。

### Phase 5：Windows 发行版同步

- Windows 独立仓库接入 contracts/factory；
- 实现 `WindowsDockerRuntime`，再实现 Native Runtime；
- 跨仓库版本契约测试、旧数据迁移演练。
回滚：Windows 发行版可固定旧 Python core 版本；主 Linux 行为不受影响。

## 6. 必须保持的兼容与安全边界

1. **API 不变**：不删除/重命名字段；新增字段必须可选；旧错误码/错误 JSON 形状尽量保持。
2. **数据不变**：实例根目录、`config.json`、`secrets.json`、`deploy-state.json`、
   `compose.yml`、`.env`、AstrBot/SnowLuma 数据路径不迁移。
3. **旧实例可启动**：v1/v2 兼容迁移逻辑不能被新适配器绕过。
4. **文件权限**：含凭据文件 0600；数据目录 0700；Windows 平台按平台策略降级但不得错误放宽。
5. **Key 不泄漏**：任何 GET/状态/错误/日志/来源摘要不得返回 API Key、token、password、secret。
6. **命令安全**：Docker/Git 等使用列表 argv、`shell=False`；插件 ID 不进入路径穿越位置。
7. **网络边界**：AstrBot 本地请求绕过代理；B 的 public_host/0.0.0.0 必须显式高风险且给出访问控制警告。
8. **UI 不改变**：`/console`、`/wizard`、`/assistant` 和 token 注入保持可用。
9. **可回滚**：每个新接口保留默认适配器；无数据迁移的模块可以直接删/替换。

## 7. 当前测试与验收基线

第一阶段新增测试：

- [`tests/test_contracts_resource_stats.py`](../tests/test_contracts_resource_stats.py)
  - `DefaultResourceStatsProvider` 满足 `ResourceStats`
  - Docker stats JSON/制表符解析
  - 目录统计不跟随符号链接
  - 宿主内存/磁盘安全降级字段
  - `StudioManager(resource_stats=fake)` 注入、mock Docker、缓存/错误字段不回归
  - 旧 `studio._directory_usage` 等私有兼容名仍可导入
  - `Store`、`DockerController`、`DeployManager`、`AssistantExtensionManager`、
    `AstrBotClient` 满足对应 Protocol
  - 自定义 `DeploymentProvider`、`UIComponentProvider` mock 可替换

验收命令：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile qqbot_studio/*.py tests/*.py
```

通过标准：全量测试通过；现有 API/旧实例/安全回归不下降；不会因为 fake runtime/stats 注入改变生产默认行为。

## 8. 剩余架构债务

- `server.py`、`deployer.py`、`studio.py` 仍是较大的聚合模块；本阶段只做了低风险统计提取。
- `DeployManager` 仍同时负责 job、Configgen、AstrBot、SnowLuma 和插件配置，Phase 1/2 继续拆。
- `assistant_ext.py` 仍将 Git 扩展管理、配置和进程生命周期放在一起；后续可拆
  `ExtensionInstaller` / `AssistantConfigStore` / `AssistantProcessRuntime`。
- `Store` 尚未替换为 Windows Credential/DPAPI 实现。
- 没有真正的 Windows Native RuntimeBackend；只有接口和目标矩阵。
- 前端仅完成第二阶段低风险提取；`console.js` 仍保留实例卡片/参数编辑器/记忆/统计渲染，
  `app.js`、`assistant.html` 仍是单体，Phase 4D 继续拆分。
- `Protocol` 的 `runtime_checkable` 只检查方法存在，不做完整签名/返回类型静态检查；
  后续应加 mypy/pyright 或 `typing.get_type_hints` 契约测试（仍保持零第三方运行时依赖）。
- Windows 独立仓库和插件模块化由对应任务负责，不在本阶段修改。

## 9. 阶段结论

Phase 0 完成了“可替换接口 + 可独立测试平台无关模块”的目标；Phase 4 第二阶段完成
控制台 API/插件配置 service 与实例设置组件边界：

- 新增并验证 8 类后端接口：`RuntimeBackend`、`DeploymentProvider`、`ExtensionManager`、
  `StateProvider`、`ResourceStats`、`ConfigStore`、`AstrBotClient`、`UIComponentProvider`；
- 将资源统计从 `studio.py` 抽到平台无关模块，并由 `StudioManager` 通过接口注入；
- 保留旧入口和兼容别名，API/数据/权限/安全策略未改变；
- 剩余前端组件（参数编辑器 DOM、实例卡片、向导、独立助手页）、Windows Native 和
  deployer/server 深度拆分留到后续阶段，未伪称已完成。
