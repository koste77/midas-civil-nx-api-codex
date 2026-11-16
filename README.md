# midas-civil-nx-api-codex

[中文](README.md) | [English](README.en.md)

面向 MIDAS Civil NX 2024 的 Codex 技能，包含参数化建模、分析结果核验说明，以及 Python 标准库客户端和离线参数生成工具。具体字段和分析行为需按目标安装版本核实。

## 功能与边界

- 使用环境变量连接软件，通过通用 REST 接口读取数据库或由任务脚本显式修改、分析模型。
- 从冻结基准生成固定拓扑下已有数值字段的 `set` / `scale` 更新，避免重复累乘。
- 根据实际结果表头解析数据，并分别核查请求成功、分析完成和结果新鲜性。
- 对配置密钥和连接地址的回显进行脱敏，阻止携带认证头的重定向。

工具不包含优化器，不会自动启动分析。离线参数生成器不创建网格、材料或截面。技能入口和详细技术参考目前为中文，本说明提供对应英文版本。

## 文件

- [SKILL.md](SKILL.md)：技能入口和工作边界。
- [agents/openai.yaml](agents/openai.yaml)：技能显示信息。
- [references/](references/)：接口、参数化流程和故障处理说明。
- [scripts/midas_api.py](scripts/midas_api.py)：客户端和离线参数生成器。
- [tests/test_midas_api.py](tests/test_midas_api.py)：不连接 MIDAS 的离线行为测试。

## 安装与环境

将本目录复制到 Codex 的技能目录下，文件夹名称保留为 `midas-civil-nx-api`。也可直接在本目录运行工具，要求 Python 3.9 或更新版本，无第三方运行依赖。

使用 Codex 时，以 `$midas-civil-nx-api` 调用技能并说明目标模型、版本、单位和期望任务。在线操作还要求可连接的 MIDAS Civil NX 安装；先在软件的 API Settings 中确认连接状态。

## 离线使用与验证

以下命令在本仓库根目录执行，不连接软件：

```powershell
python -X utf8 -B scripts/midas_api.py --help
python -X utf8 -B -m unittest discover -s tests -v
```

根据[参数化流程](references/parametric-workflow.md)准备 `baseline.json`、`parameters.json` 和 `values.json` 后，可离线生成更新；这些是使用者输入文件，并未随仓库提供。

```powershell
python -X utf8 -B scripts/midas_api.py plan --baseline baseline.json --spec parameters.json --values values.json --output updates.json
```

输出文件必须尚不存在，工具不会覆盖已有输出。离线测试覆盖请求重试边界、分析完成判定、脱敏、参数更新不累乘和结果表解析等行为。

## 在线只读检查

实际使用客户端或 `probe`、`get` 时，由使用者在本机设置以下环境变量。本仓库不提供其真实值，不接受命令行传入密钥。

| 环境变量 | 用途 |
|---|---|
| `MIDAS_BASE_URL` | 目标软件提供的连接地址，包含产品根路径 |
| `MIDAS_API_KEY` | 目标安装提供的认证密钥，作为 `MAPI-Key` 请求头发送 |

确认环境变量后，可执行只读检查：

```powershell
python -X utf8 -B scripts/midas_api.py probe
python -X utf8 -B scripts/midas_api.py get UNIT --output unit.json
```

模型写入和分析需由任务脚本显式调用。只读 GET 可有限重试；修改或分析超时后需先核查实际状态。详见[接口契约](references/api-contracts.md)和[故障处理](references/troubleshooting.md)。

