# 开发说明

面向要改这个仓库的人。使用者看 [README.md](README.md)。

## 只有一条部署路径

这个仓库**没有 CDK**。模板是手写的自包含 CloudFormation，全部原生资源，
没有 Lambda、没有自定义资源、没有资产、不需要暂存桶，所以也没必要再维护一份 CDK stack。

```
./deploy.sh
  ├─ create-stack / update-stack   cloudformation/managed-kb-eval.yaml
  ├─ aws s3 sync sample-data/docs/ → s3://<桶>/docs/
  └─ scripts/ingest.py             StartIngestionJob + 等到真的能搜
```

文档不打包进模板、也不发 GitHub release，因为使用者本来就要 clone 仓库，
`s3 sync` 是最短路径。代价是模板本身不能单独 1-Click 部署出一个可用的 KB
（建出来是空的），这是有意的取舍。

## 代码结构

```
cloudformation/managed-kb-eval.yaml   S3 桶 + IAM 角色 + 托管 KB + 连接器数据源
sample-data/docs/                     5 篇中文合成语料
sample-data/questions.json            11 题 ground truth
scripts/common.py                     SDK 自举、stack 输出、文档名提取
scripts/retrieval.py                  三档检索的统一封装
scripts/ingest.py                     建索引并轮询到可搜
scripts/eval.py                       打分与报表
scripts/ask.py                        单题交互
scripts/run.sh                        统一解析 python 的启动器
```

`deploy.sh` / `eval.sh` / `ask.sh` / `status.sh` / `cleanup.sh` 都是薄封装，
真实逻辑只在 `scripts/` 里一份，避免演示脚本和开发脚本两套逻辑漂移。

## SDK 自举

托管知识库和 `AgenticRetrieveStream` 是较新的服务模型更新，CloudShell 自带的 boto3
通常不认识。`common.ensure_sdk()` 的做法：

1. **能力探测，不比版本号。** 用 botocore 的 loader 直接读服务模型，检查
   `CreateKnowledgeBase` 的 `type` 枚举里有没有 `MANAGED`，以及
   `bedrock-agent-runtime` 的 operation 里有没有 `AgenticRetrieveStream`。
   走 loader 而不是建 client，是为了不依赖 region 和凭证。
2. 不满足就在仓库里建 `.venv-sdk`，装 `boto3>=1.43.89`，然后用
   **`subprocess.run` + `sys.exit(returncode)`** 重新拉起自己，靠环境变量标记防止套娃。

不要改成 `os.execv`：在 Windows 上被 exec 的子进程输出会丢，父进程还返回 0，
看起来成功但什么都没打印。

## 踩过的坑

**托管 KB 的 `Retrieve` 用 `managedSearchConfiguration`，不是 `vectorSearchConfiguration`。**
两者在同一个 `retrievalConfiguration` 下并存，网上所有托管化之前的例子都用后者。
托管重排的开关 `rerankingModelType`（`CUSTOM` / `MANAGED` / `NONE`）在
`managedSearchConfiguration` 里。

**`s3Location.uri` 是 percent-encoded 的 https URL，不是 `s3://`。** 中文文件名会变成
`02-%E9%94%99%E8%AF%AF...`，直接拿去和 ground truth 比会静默全部不匹配。
metadata 里有干净的 `_document_title`，优先用它；兜底路径要
`urllib.parse.unquote`。同一条结果里 `documentId` 反而是没编码的 `s3://` 形式。

**`AgenticRetrieveStream` 的 `results[]` 里没有 `location`**，只有 `content`、
`metadata`、`sourceRetriever`。想统一处理两个 API 的返回，来源只能从 metadata 取。
`common.doc_names()` 就是为了吃下这个差异。

**流式返回里的异常是 stream 成员，不是抛出来的错误。** `AgenticRetrieveStream` 的输出
schema 里有 `validationException`、`throttlingException` 等一堆成员。只 `for event in
stream` 挑自己认识的键，遇到错误会安静地当成空结果。`retrieval._agentic()` 里显式检查
了 `key.endswith("Exception")`。

**CloudFormation 建数据源但不摄取。** 光建栈的话知识库是空的，每个问题都返回零结果。
必须自己调 `StartIngestionJob`。

**`COMPLETE` 不等于可搜，要轮询真实检索结果。** 这次实测里 job 报 COMPLETE 之后
探针查询立刻就有 3 个 chunk，没等到追赶期；但既然 API 在索引没就绪时返回的是
**空列表而不是报错**，只看状态字段就可能得到稳定复现的假失败。`ingest.py` 因此轮询
的是 `Retrieve` 的真实返回。

**`metadata._language_code` 把中文语料判成了 `en`。** 5 篇文档全部如此。
检索分数正常（相关 chunk 0.99+），未观察到实际影响，但如果你要按语言做元数据过滤，
先验证这个字段而不是直接信它。

**`ConnectorParameters` 里所有值必须是字符串。** CloudFormation 的 `Json` 属性会把
数字变成字符串，服务端随后报类型错误。模板里 `version: "1"` 的引号是必须的。

**评测本身要先自证有区分度。** 首轮 `--top-k 10` 跑出三档全 100%。5 篇语料取 10 个
chunk 等于把整个语料库都召回（实测每题 docs=4~5 / 共 5），任何检索策略都满分。
默认值因此改成 3。换语料后重新校准这个值，否则报出去的是一个测不出差别的指标。

## 本地跑（非 CloudShell）

Windows 上 `.sh` 跑不了，直接调 python：

```powershell
python -m venv .venv-sdk
.venv-sdk\Scripts\pip install "boto3>=1.43.89"
chcp 65001                      # 否则控制台输出中文会花屏
$env:PYTHONIOENCODING="utf-8"
.venv-sdk\Scripts\python scripts\eval.py --top-k 3
```

注意 PowerShell 的管道会重新编码：`... | Select-Object -Last 60` 会把中文变成乱码，
而直接输出或重定向到文件不会。看到乱码先怀疑管道，不要怀疑脚本。

## 改模板要注意

- `EmbeddingModelType` 是 create-only 语义（服务侧不允许改），改这个参数会导致
  知识库替换。想切换嵌入模型请直接 `./cleanup.sh` 再重建。
- `CUSTOM` 嵌入模型会让托管重排器不可用，也就是 `rerank` 那一档失效。
  改默认值之前先想清楚评测还剩几档。
- 桶里有对象时 CloudFormation 删不掉桶，而模板里没有清桶的 Lambda，
  所以 `cleanup.sh` 先 `s3 rm --recursive` 再删栈。加资源时别破坏这个顺序。

## 待验证

以下没测过，做方案前必须先确认：

- 文档级访问控制（`CheckIngestedDocumentAcl` / `GetIngestedDocumentAcl` /
  `userContext.userId`）在托管 KB 上的实际行为。
- `memoryConfiguration` 把 AgentCore Memory 接进多跳检索的效果。
- `FullDocumentExpansion` 本次 11 题都没触发。按[官方文档](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)
  它在模型判断需要整篇文档时才出现（摘要、确认完整性、取特定章节），会调
  `GetDocumentContent`。本仓库的题目都是精确事实查找，所以没触发是合理的；
  想复现这一步得加摘要类问题，还要确保调用方有 `bedrock:GetDocumentContent` 权限。
- 大语料（千篇量级）下三档的差距是否还是这个形状。小语料对纯向量检索是有利的。
- 多跳内部检索轮次的方差来源。三次跑里 M2 出现过 `Retrieval x4` 和 `Retrieval x8`
  两种结果，其他题的轮次都稳定。报表里 `report()` 只统计实际产生了 row 的档位，
  所以 `--modes` 传子集不会崩，这是修过的一个 bug，改报表时别改回去。
- 多模态摄取（`mediaExtractionConfiguration`）。
