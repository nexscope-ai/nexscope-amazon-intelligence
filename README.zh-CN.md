# NexScope Amazon Intelligence

[English](README.md)

这是一个 OpenAI Codex 插件，包含 40 个 Amazon 电商技能，覆盖商品、市场、关键词、竞品、评论、销量、政策、类目和广告分析流程。

## 项目内容

- `.codex-plugin/plugin.json`：插件清单和公开上架元数据
- `skills/`：40 个可独立加载的 Amazon 技能
- `assets/`：插件图标及明暗主题 Logo
- `SUBMISSION.md`：公开上架文案、测试用例和发布方必须完成的发布事项

技能同步自 [`nexscope-ai/nexscope-ecommerce-skills`](https://github.com/nexscope-ai/nexscope-ecommerce-skills) 的提交 `e380b29273c2d1cd77e3335c2979c6310f484db4`。此前本地快照缺少的 `amazon-ads-manager` 和 `amazon-category-lookup` 已补齐。

## 身份验证

NexScope 付费订阅用户自动获得插件权益。浏览器设备授权会校验订阅状态，将当前设备绑定到账号已有的 API Key，并且只把可撤销的安装令牌保存到 macOS 钥匙串或 Windows 凭据管理器：

```sh
python3 runtime/installer.py connect
python3 runtime/installer.py update
```

所有 Skill 脚本默认优先读取已连接的系统凭据。开发者如需保留原环境变量方式，必须显式指定：

```sh
export NEXSCOPE_AUTH_MODE="environment"
export NEXSCOPE_PROXY_BASE="https://api.nexscope.ai/"
export NEXSCOPE_API_KEY="<your_api_key>"
```

不要将 API 密钥提交到仓库，也不要把密钥写入提示词、日志、测试数据或问题报告。系统凭据库不可用时不会降级为明文保存。

账号没有有效订阅时，授权页不会激活设备，并会引导用户完成订阅。插件不会另行生成或展示 API Key。

其他本机命令为 `status`、`doctor`、`disconnect` 和 `uninstall`。仅在服务不可达时使用 `disconnect --local-only`；此时远端席位仍会占用，需在账号设备页解绑。

## 商业发行构建

准备经过审核的双平台离线 wheels 后执行：

```sh
python3 scripts/build_release.py 1.1.0 \
  --wheel-dir /path/to/reviewed-wheels \
  --trusted-keys /path/to/reviewed-trusted-keys.json \
  --artifact-base-url https://github.com/lambbell/nexscope-amazon-intelligence/releases/download/v1.1.0
python3 scripts/self_check.py
```

构建会生成冻结合同要求的三份平台/架构加密包、严格 ManifestV1 元数据和权限为 `0600` 的私密载荷密钥文件。审核后的公钥会在构建时注入，无需把生产信任材料提交到 Git。将私密文件导入 NexScope 发布服务后应安全删除。

## 本地安装

商业发行包包含薄安装器、加密载荷、已审核的离线依赖和安装说明。服务签发具体版本的载荷密钥后，安装器才会落盘明文技能。

## 发布方

ECOCREATE TECHNOLOGY PTE. LTD.  
支持邮箱：[service@nexscope.ai](mailto:service@nexscope.ai)  
[网站](https://www.nexscope.ai/) · [隐私政策](https://www.nexscope.ai/privacy) · [服务条款](https://www.nexscope.ai/terms)
