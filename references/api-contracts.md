# API 契约与版本核实

以下为 CIVIL NX 2024 工作流的常用接口形状。字段、端点大小写和结果类型均需在目标安装上回读验证；不据此宣称所有 2024 小版本均兼容。

## 查找版本资料

从软件帮助或对应版本的官方资料检索以下条目；本发布副本不内置外部链接或服务地址。

- 2024 V1.1 Release Note：版本能力背景，不是逐字段接口契约。
- API Settings：连接设置和 MAPI-Key 请求头。
- MIDAS API Online Manual：持续更新，注意条目适用版本和产品。
- GET 样例及 info 接口：可用时查询 `GET /info/db/表名`；目标安装的 GET 样例优先。
- Perform Analysis、Section Manager - Stiffness：分析调用与截面刚度设置。

## 连接与路径

Base URL 已包括产品根路径，例如软件显示的 `/civil`，客户端只追加端点，不重复拼接。头部 `MAPI-Key` 的值来自环境变量。保持 TLS 验证；认证或连接故障不能通过关闭证书校验解决。

文档的 Argument 是 **运行 CIVIL 的主机上的绝对文件路径**，不是远程代理容器路径。非 ASCII 路径使用 UTF-8 JSON，不能将 Python 的路径转义或 JSON 字符串直接当 shell 转义。

下表使用小写端点形式；目标版本资料可能使用大写，不类推未知端点也支持大小写互换。

| 方法与端点 | 请求/响应要点 |
|---|---|
| POST /doc/open | `{"Argument": work_model_path}`，变量为运行 CIVIL 主机上的绝对路径；切换前处理当前文档保存状态 |
| POST /doc/saveas | 同样传绝对目标路径；不能覆盖未授权文件 |
| POST /doc/save | `{}`；保存当前任务文档 |
| POST /doc/anal | `{}`；普通分析。不是所有分析类型都用相同参数 |
| POST /doc/export | `{"Argument": snapshot_path}`，变量为运行 CIVIL 主机上的绝对路径；可选，不能成为每轮计算的必经操作 |
| GET /db/UNIT | 如 `{"UNIT":{"1":{...}}}`，不是 Assign |
| GET /db/NODE | 如 `{"NODE":{"1":{"X":0,"Y":0,"Z":0}}}` |
| POST 或 PUT /db/表名 | `{"Assign":{"字符串ID":完整记录}}`；POST 新增、PUT 修改，具体语义查相应表 |
| DELETE /db/表名/ID | 删除会影响引用关系；先列明任务内待删编号和依赖，不用重建全部数据库代替局部更新 |
| POST /post/table | `{"Argument":{...}}`；响应由请求表名及具体表类型决定 |

UNIT、STYP 等必要单例通常只支持 GET/PUT。一般 upsert 过程为 GET 已有编号 → 生成差异 → 已有编号 PUT、新编号 POST → 回读。不能把所有 PUT 视为无条件新增。

对于 ITEMS 列表，先核对 ID、GROUP_NAME、IPART 等业务标识；列表索引不是稳定的业务编号。读取冻结基准以后才能记录索引，并核对恢复时记录结构未变。

## 参数化常用表

| 表 | 用途及容易混淆的地方 |
|---|---|
| NODE / ELEM | 坐标、类型、连接、材料/截面引用；重划网格后更新测点映射和依赖 |
| MATL | 显式材料的待核实示例为 PARAM[0].P_TYPE=2，含 ELAST、POISN、DEN；不要套用到数据库材料或其他类型 |
| SECT / THIK | 截面及板厚；需要当前截面类型的模板 |
| SECF | IYY_SF、IZZ_SF 是局部轴弯曲倍率，IXX_SF 是扭转倍率；AREA_SF、剪切、WGT_SF 等分别核对 |
| CONS / RIGD | 边界与刚性连接；排除重复从节点、闭环及无意过约束 |
| STLD / BMLD / CNLD | 工况与荷载；不能仅修改数值而留下旧单元或车道引用 |
| ACTL / EIGV | 应力恢复/分析控制及模态设置；具体字段从实际 GET 样例核实 |

SECF 中 bDiffIJ 为真时，需核实 J5 是否对应 I 端 IYY_SF 的 J 端倍率。此映射需结合目标版本、截面和界面验证，不能对未知记录强制补 J5。PSC 应变恢复应核对 ACTL 的 CSECF 是否与缩放口径一致。

## 结果请求与解析

下面是需在目标安装验证的位移请求示例。`node_ids`、`load_case_names` 由当前模型获取；静力工况选择可能使用 `名称(ST)`，返回 Load 字段可能只有名称，匹配时显式处理。

```python
payload = {"Argument": {
    "TABLE_NAME": "Displacements",
    "TABLE_TYPE": "DISPLACEMENTG",
    "UNIT": {"FORCE": "N", "DIST": "mm"},
    "STYLES": {"FORMAT": "Scientific", "PLACE": 12},
    "NODE_ELEMS": {"KEYS": node_ids},
    "LOAD_CASE_NAMES": load_case_names,
    "COMPONENTS": ["Node", "Load", "DX", "DY", "DZ", "RX", "RY", "RZ"]
}}
result = client.request("POST", "/post/table", payload)
rows = table_rows(result["Displacements"])
```

典型响应块为 `{"HEAD":["Index","Node",...],"DATA":[["1","27",...]]}`，DATA 包括与 Index 对应的值，不能擅自去掉首列。检查表头唯一、行宽一致、目标节点/工况齐全，不默默丢弃无法匹配的行。SUB_TABLES 需要先按实际结构定位再解析。

其他需核实的结果表类型：

- REACTIONG：支持反力及总荷载/力矩核对。
- BEAMSTRESSPSC：PSC 梁应力，带 PartI/PartJ、Pos-1…Pos-4。不是通用钢梁应力类型。
- EIGENVALUEMODE：模态，可能包含 EIGENVALUE ANALYSIS 等子表。
- MASS_SUMMARY_Z：质量汇总，单位需核对，不由表名猜测。

不同表分量名不能相互套用。应力从输出应力单位转换后除以对应材料 E 才得到应变；不要将 N/mm²、kN/m²、Pa 混用。API 显示精度或 OUT 更多位数不等于试验精度。

## 求解成功

随附 `require_analysis_complete` 仅识别普通分析消息 `MIDAS Civil NX Command Complete`。需确认目标安装确实使用此消息；未知返回需检查，不能默认成功。

此外还需检查：求解器错误/警告、当前模型与参数回读、结果属于本轮。OUT 的时间戳重写可作证据之一；同一参数重算内容可能完全一致，不要求其散列一定变化。某些安装的求解文件不同，应记录实际产物，而非捏造 OUT 路径。
