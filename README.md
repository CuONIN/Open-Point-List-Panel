# ZF Saarbrücken · EOL 现场问题工作台

Windows x64 本地中文应用。双击根目录 **启动 OPL.cmd**，启动器会配置项目自己的 Python 环境并打开浏览器。已配置的电脑可以离线使用。服务只监听本机，默认地址为 `http://127.0.0.1:8765/`。首次配置可联网下载已锁定的依赖，或从已配置的电脑复制 `.runtime/cache/` 后运行 `启动 OPL.cmd -Offline`。退出运行服务的命令行窗口即可关闭工作台。

## 从 Excel 到现场派工单

1. 把客户工作簿放在 `EOL/List/ZF/`，把豪森的中英双语或宏工作簿放在 `EOL/List/Haosen/`。首次启动会建立基线。后续可在“导入与核对”预览再提交，或双击 **同步 Excel.cmd** 扫描新增版本。指定日期或同日修订可用下方命令。
2. 打开“问题清单”，选择范围和筛选标签，再点“应用筛选”。同一项勾选多个值时取并集。不同筛选项同时生效。筛选框默认勾选可见值。我方未结默认排除“客户已关闭”“转纯 ZF”和纯 ZF 责任方，可手动勾回；取消某栏全部选项会得到空清单。客户号可输入 `102,1234,1203`，也支持中文逗号，裸号包含其全部字母后缀，带后缀只查该号。“客户提出者”和“我方负责人”是两个独立字段。点击“客户号”表头切换编号升降序。
3. 点击问题号码看完整双语描述、逐段客户行动、现场反馈、证据与变更历史。关闭弹窗会返回原筛选和页码。点击行内状态可登记会议决定。取得客户验收确认时，可选“客户已关闭”，填写确认日期、记录人及邮件或会议纪要凭据。客户表中的 `erledigt` 也能自动关闭。
4. 选 **现场精简（中文优先）** 给钳工和电工派工，或选 **中英对照** 核对原文。两种内容都能导出 A4 横版或竖版的 PDF、Excel，覆盖整组筛选结果并沿用当前编号顺序。精简版分为编号及类别、工位号和名称、问题描述、近期行动、跟进信息五栏。最右栏列出状态、负责人和计划，有关联问题或投产单号时一起列出。没有译文的字段保留客户原文。双语版将中文、原文和行动分栏。PDF 使用本机 Edge 或 Chrome，Excel 工作簿保存纸型、方向、重复表头和可自动展开的行高。
5. “项目概览”的两块周明细可独立滚动。实施清单合并本周排期和截至统计日最近一次在本周进入“整改中”的项，每个问题显示一次。“项目周报”生成可复制的周报。“专项任务”登记节拍整改、安全检测和周报工作。

工作簿将纸型写为 A4。Excel 的“A4/Letter 纸张缩放”选项和打印驱动仍可影响实际出纸。需要固定物理纸型时，同一筛选条件的 PDF 保存了精确的 A4 页面尺寸。

区域可选 S1、S2、AGV 和 General。AGV 统一汇总。要看某台设备，可按 **24821220** 或 **24821221** 筛选机器号。客户原始写法及原始提出者姓名保存在来源快照中。Tobias Gleser 可作为客户提出者筛选。我方多人负责人字段保留其他共同负责人。

## 数据和命令

同一台电脑更新代码时，关闭运行中的 OPL，在原仓库目录执行 `git pull --ff-only`，然后双击 **启动 OPL.cmd**。Git 忽略本机数据库、附件和来源文件，此次更新沿用原数据库格式。如果是另一台全新电脑，可将内部交接备份中的 `data/` 和 `EOL/` 解压到新下载的项目目录，再启动。已有现场记录的电脑直接使用自己原有的库。

主数据文件为 `data/opl.sqlite3`。源版本在 `data/sources/`，手工上传的证据和既有 `EOL/Feedback/` 文件由数据库索引。通过备份命令一起保存数据库、来源工作簿和关联证据。恢复命令使用尚不存在的新目录。

```powershell
.\.venv\Scripts\python.exe -m opl sync
.\.venv\Scripts\python.exe -m opl sync ".\EOL\List\ZF\customer.xlsx" --version 2026-11-23 --revision 1
.\.venv\Scripts\python.exe -m opl backup
.\.venv\Scripts\python.exe -m opl restore ".\backups\备份文件.zip" "E:\OPL-恢复"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

应用优先复用源表译文。需要另外翻译时，在本机设置 `OPENAI_API_KEY` 后重新启动，可选用 `OPENAI_MODEL` 指定可用模型。使用界面的翻译按钮才会提交待译内容。日常筛选、打印、导入和现场编辑均在本机完成。

可选浏览器回归脚本为 `tests/run_browser_flow.py`。它在临时目录建库，运行真实状态切换、附件上传和任务关联。开发机需有可供 Node.js 引用的 Playwright 和本机 Edge。`OPL_TEST_NODE` 可指定 Node 可执行文件，`OPL_TEST_BROWSER` 可指定浏览器位置。

## 维护

| 目录 | 用途 |
| --- | --- |
| `opl/excel.py`、`stations.py`、`people.py`、`importer.py` | 解析来源、规范化字段、匹配和版本导入 |
| `opl/store.py`、`workflow.py`、`records.py` | SQLite 事务与升级、状态机、反馈和专项任务 |
| `opl/queries.py`、`exports.py`、`export_pdf.py`、`export_xlsx.py` | 清单与统计查询、共享派工单字段、两种打印格式 |
| `opl/web.py`、`templates/`、`static/` | Flask 接口和浏览器界面 |
| `scripts/bootstrap.ps1`、`requirements.lock`、`tests/` | 环境配置、精确依赖版本与哈希、回归测试 |

使用 CPython 3.14.9、SQLite 3.50.4、Flask 3.1.3、Waitress 3.0.2、openpyxl 3.1.5 和 tzdata 2026.2。所有直接、间接依赖的版本与校验值见 [requirements.lock](requirements.lock)。Git 忽略生产工作簿、数据库、备份、附件、缓存、虚拟环境和本机密钥。业务矩阵、统计口径及验收用例见 [开发设计](doc/EOL-问题管理开发设计.md)。
