# Bedrock 托管知识库上手教程：从零跑通，并用数据验证该开哪些开关

做企业文档问答，过去绕不开一堆自建：选嵌入模型、定向量维度、开一个向量库、
写切分和入库、调重排、再想办法处理「答案分散在多篇文档里」的多跳问题。
这些活儿和业务价值无关，但每一件都能卡住一周。

Amazon Bedrock 的**托管知识库**（Managed Knowledge Base）把这一整段收进了服务里：
存储、索引、检索基础设施由服务管理，默认连嵌入模型都不用你选
（[官方文档](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-create.html)）。
AgentCore 控制台里新出现的「知识库（KB）」入口指向的就是它。

**我的建议是：做 RAG 就从托管知识库起步。** 不是因为它省事——省事只是结果——
而是因为它把 RAG 里最难调的三件事（嵌入、重排、多跳规划）做成了托管开关，
你可以先用默认值上线，再拿自己的数据决定要不要动。

这篇分两部分：先是一份可以照着做完的完整教程，然后是我在 us-west-2 实测三次的
数据，用来回答「这些开关到底该怎么配」。

配套代码在
[managed-kb-eval-demo](https://github.com/quanquan1996/managed-kb-eval-demo)。

---

# 第一部分：教程

## 1. 它替你托管了什么

先对齐概念，后面配参数时会用到。

| 你原本要做的 | 托管知识库里的形态 |
|---|---|
| 选嵌入模型、定维度、管配额 | 默认**托管嵌入**，不用选型，也不占你的模型配额 |
| 开向量库（如 OpenSearch Serverless 集合） | 服务托管向量存储，**没有按小时计费的集合** |
| 自己写切分、入库、增量同步 | 数据源连接器 + `StartIngestionJob` |
| 自己接一个重排模型 | `rerankingModelType: MANAGED`，一个枚举值 |
| 自己写多跳：拆子问题、多轮检索、判断够不够 | `AgenticRetrieveStream` 一个 API |

托管嵌入还有一个常被忽略的好处：**不需要在 Bedrock 控制台申请模型访问**。
这对 demo 和 PoC 是实打实的提速，客户账号不用先走一遍模型开通流程。

两个**创建时就要定、之后改不了**的决定，先记住：

- **嵌入模型类型创建后不可更改。** 想从托管嵌入换成自定义（或反过来），
  只能新建一个知识库。
- **一旦使用自定义嵌入模型，托管重排器就不可用。** 想用托管重排，
  创建时就得用默认的托管嵌入。自定义嵌入须为 1024 维、float32，
  可选 Titan Text Embeddings V2、Cohere Embed v3/v4、Nova Multimodal Embeddings。

这两条都写在[创建托管 KB 的官方文档](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-create.html)里。
我把它们放在教程最前面，是因为它们决定了后面第 6 节那张对照表里有几档可选。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| Region | 托管知识库并非所有 region 可用。本文全部在 `us-west-2` 实测 |
| 权限 | 要能创建 S3、IAM 角色、Bedrock 知识库。Demo 账号用 `AdministratorAccess` 最省事；生产请按下面的最小权限收敛 |
| 模型访问 | **不需要申请。** 嵌入、重排、多跳规划三个角色全部服务托管 |
| 工具 | 只要 AWS CloudShell。里面自带 aws cli、python3、git |

调用方（不是知识库的角色，是你的应用身份）做检索需要的权限，
官方文档给了明确清单。纯 `Retrieve` 只需要 `bedrock:Retrieve`；
用多跳检索则需要这一组
（[来源](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "bedrock:AgenticRetrieveStream", "Resource": "*" },
    {
      "Effect": "Allow",
      "Action": ["bedrock:Retrieve", "bedrock:GetDocumentContent"],
      "Resource": "arn:aws:bedrock:<region>:<account-id>:knowledge-base/*"
    },
    { "Effect": "Allow", "Action": "bedrock:InvokeModelWithResponseStream", "Resource": "*" }
  ]
}
```

注意 `bedrock:GetDocumentContent`：多跳检索在模型判断需要整篇文档时会去取全文，
少了这条权限，那一步会失败。用 guardrail 或 AgentCore Memory 还要各自再加几条，
文档里都列了。

## 3. 部署（约 5 分钟）

打开 [AWS CloudShell](https://console.aws.amazon.com/cloudshell/home)，粘贴：

```bash
git clone https://github.com/quanquan1996/managed-kb-eval-demo.git
cd managed-kb-eval-demo
./deploy.sh
```

脚本做三件事，顺序很重要：

1. 建 CloudFormation stack —— 一个 S3 桶、一个 IAM 角色、一个托管知识库、一个数据源。
   实测 67 秒。
2. `aws s3 sync` 把 `sample-data/docs/` 传到桶的 `docs/` 前缀下。
3. 调 `StartIngestionJob` 建索引，然后等到**真的能查出结果**为止。实测约 2 分半。

第 3 步单独说一下，因为这是自己写部署脚本时最容易漏的：
**CloudFormation 只建数据源，不会摄取。** 光建完栈，知识库是空的，
每个问题都返回零结果，而且不报错。摄取必须显式调
[`StartIngestionJob`](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-create.html)。

预期输出（截取关键行）：

```
Stack:      managed-kb-eval
Region:     us-west-2
Embeddings: MANAGED

Uploading the corpus to s3://managed-kb-eval-corpusbucket-xxxx/docs/
Ingestion job WAYRYKNRP0 started. Indexing the corpus.
  status STARTING
  status IN_PROGRESS
  ...
  status COMPLETE  {'numberOfDocumentsScanned': 5, 'numberOfNewDocumentsIndexed': 5,
                    'numberOfDocumentsFailed': 0, ...}
Waiting for the index to serve queries.
  searchable: probe '错误码 E207' returned 3 chunks.
```

看到 `numberOfDocumentsFailed: 0` 和最后那行 `searchable:` 就成了。

**为什么还要等一次「searchable」？** 因为摄取作业报 `COMPLETE` 之后索引不一定立刻可查，
而这种情况下 `Retrieve` 返回的是**空列表，不是报错**。我这次实测 COMPLETE 后立刻就有结果，
没等到追赶期；但既然失败形式是空列表，脚本轮询的就该是真实检索结果，而不是状态字段。
这是个便宜的保险，建议你自己的部署脚本也这么写。

想用控制台走一遍的话，路径是 **Amazon Bedrock AgentCore → 内容工具 → 知识库（KB）
→ 创建托管 KB**，然后连数据源、同步。控制台和 API 建出来的是同一种资源。

## 4. 第一次查询：先看懂多跳的流水线

```bash
./ask.sh "华东一台 EM-300，2023 年 5 月出厂，反复上报 E207，要换表吗？需要报备吗？"
```

语料是虚构公司「AnyCompany 智能电表」的 5 份中文售后文档（全部为合成数据）。
这道题的答案**不在任何单篇文档里**，需要拼四篇：错误码手册说 E207 是零点漂移、
判据在固件 4.2.1 之前偏严；版本文档说 2023 年 5 月出厂的机器固件是 4.0.x/4.1.0、
不含这个修复；保修政策说固件缺陷免费升级、不占部件保修额度；
区域文档说华东只有整表更换要提前 5 个工作日报备，固件升级不用。

输出会先打印服务内部的步骤：

```
Pipeline steps reported by the service:
  SpeculativeRetrieval     IN_PROGRESS
  SpeculativeRetrieval     SUCCEEDED
  Planning                 IN_PROGRESS
  Planning                 SUCCEEDED

Answer:
（一段带小标题的中文回答，逐条给出：不需要换表、先查固件版本、
  低于 4.2.1 按固件缺陷免费升级、升级后观察 7 天、固件升级无需向计量院报备……）

Sources (10 chunks):
  01-产品型号与固件版本.md
  02-错误码手册.md
  03-保修政策.md
  04-区域合规与报备要求.md
Elapsed: 23.5s
```

四篇文档全部命中，答案正确。这里值得展开的是那几个步骤名，
官方文档对每一步都有定义（[来源](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)）：

| 步骤 | 含义 |
|---|---|
| `SpeculativeRetrieval` | **规划之前**先用原始查询检索一次，目的是降低延迟；配多个知识库时它还兼做探测、决定路由到哪个检索器 |
| `Planning` | 模型分析查询、拆成子问题；拿到结果后再判断够不够，不够就继续加轮次，直到达到 `maxAgentIteration` |
| `Retrieval` | 子问题实际打到数据源上 |
| `FullDocumentExpansion` | 模型认为需要整篇文档时（比如要做摘要、要确认完整性），调 `GetDocumentContent` 取全文 |
| `SessionHistoryLoad` | 配了 AgentCore Memory 的 `sessionBinding` 时，先恢复上一轮会话 |

搞清这张表很有用：**这就是延迟的账单明细**。后面第 7 节会看到，
简单问题和复杂问题在这里的行为完全不同。

## 5. 跑评测

```bash
./eval.sh
```

11 道题 × 3 档，实测 138~140 秒。题目在 `sample-data/questions.json`：
4 道单跳、5 道多跳、2 道故意问库里没有的东西（采购单价、代理商申请）。
每题标了两样标准答案：**必须命中哪几篇文档**、**答案里必须出现哪些事实**。

输出分三块：逐题明细、两张对照表、汇总。

```
[M1] 华东一台 EM-300 智能电表，2023 年 5 月出厂，……
  plain      0.7s   3 chunks  docs=2  recall= 50.0%  facts= 75.0%
  rerank     0.7s   3 chunks  docs=2  recall= 50.0%  facts= 75.0%
  agentic   17.3s   3 chunks  docs=4  recall=100.0%  facts=100.0%
```

## 6. 三档分别怎么调

评测里的三档就是三组参数。这一节是可以直接抄进你自己代码的部分。

### 6.1 纯向量检索

**这里是最容易踩的一处，而且官方文档专门出了提示：托管知识库要用
`managedSearchConfiguration`，`vectorSearchConfiguration` 只适用于自建知识库**
（[来源](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-create.html)）。
托管化之前的所有示例代码用的都是后者，照抄会直接报错。

```python
response = client.retrieve(
    knowledgeBaseId=kb_id,
    retrievalQuery={"text": question},
    retrievalConfiguration={
        "managedSearchConfiguration": {
            "numberOfResults": 3,
            "rerankingModelType": "NONE",
        }
    },
)
chunks = response["retrievalResults"]
```

### 6.2 打开托管重排

同一个调用，只改一个枚举值：

```python
"rerankingModelType": "MANAGED"   # 可选 CUSTOM / MANAGED / NONE
```

没有别的了。不用建资源、不用配模型、不用申请访问。
第 7 节的数据会说明这是性价比最高的一个改动。

### 6.3 多跳检索

```python
response = client.agentic_retrieve_stream(
    messages=[{"role": "user", "content": {"text": question}}],
    retrievers=[{
        "configuration": {
            "knowledgeBase": {
                "knowledgeBaseId": kb_id,
                "retrievalOverrides": {"maxNumberOfResults": 3},
            }
        }
    }],
    agenticRetrieveConfiguration={
        "foundationModelType": "MANAGED",
        "rerankingModelType": "MANAGED",
        "maxAgentIteration": 5,
    },
    generateResponse=True,
)

for event in response["stream"]:
    if "traceEvent" in event:
        attrs = event["traceEvent"].get("attributes", {})
        print(attrs.get("step"), attrs.get("status"))
    elif "result" in event:
        results = event["result"].get("results", [])
        answer = (event["result"].get("generatedResponse") or {}).get("answer", "")
    else:
        # 建模过的异常是 stream 成员，不会抛出来
        for key, value in event.items():
            if key.endswith("Exception"):
                raise RuntimeError(f"{key}: {value.get('message')}")
```

四个要点：

- `retrievers` 最多 5 个，每个指向一个托管知识库；可以带元数据过滤和结果上限。
- `generateResponse` **默认就是 `true`**，会直接给你一段带 citation 的答案。
  只想要 chunk 不要答案就显式设 `false`。
- `maxAgentIteration` 是上限而不是目标。文档明确提示**调低它可能让代理提早停止，
  复杂查询的准确率会下降**，别拿它当省钱的第一个旋钮。
- **最后那个 `else` 分支不要省。** `validationException`、`throttlingException`
  这些是流里的成员，不是抛出的异常。只挑自己认识的键，出错时会安静地当成空结果。

另外多跳只支持**托管**知识库，自建的用不了。

## 7. 换成你自己的数据

这是整篇里最值得你花时间的一节 —— 前面的数字是我语料上的，你需要的是你语料上的。

### 换文档

把文件放进 `sample-data/docs/`（支持 PDF、Word、Markdown、纯文本等），
重跑 `./deploy.sh`。它会同步到 S3 并重建索引，可以反复跑（第二次实测约 60 秒，
未变更的文档不会重复索引）。

想清掉示例文档再放自己的：

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name managed-kb-eval \
  --query "Stacks[0].Outputs[?OutputKey=='CorpusBucketName'].OutputValue" --output text)
aws s3 rm "s3://$BUCKET/docs/" --recursive
aws s3 sync ./my-docs/ "s3://$BUCKET/docs/"
./deploy.sh
```

S3 之外，托管知识库还支持 Box、Confluence（Cloud 与 Data Center）、SharePoint Online、
Google Drive、OneDrive、ServiceNow、Web Crawler 和自定义连接器。

### 换问题

改 `sample-data/questions.json`：

```json
{
  "id": "M1",
  "type": "multi",
  "question": "你的问题",
  "expected_docs": ["某文档.md", "另一篇.md"],
  "expected_facts": [["5 年", "五年"], ["核心部件"]]
}
```

- `expected_docs`：回答这题**必须**召回的文档，用来算文档召回率。
- `expected_facts`：外层「都要满足」，内层「满足一个即可」。上例要求同时出现
  （`5 年` 或 `五年`）和 `核心部件`。**内层要写宽容一些** ——
  匹配是子串比对而不是模型评判，同义改写会被判成没命中。
- `type` 填 `single` / `multi` / `out_of_scope`。填 `out_of_scope` 时两个 expected
  留空，评测会改为检查它有没有老实说不知道。

出题的关键是**让多跳题真的需要多跳**：把答案的各个部分分散到不同文档里。
如果所有题的答案都在单篇文档中，你会测不出三档的差别，然后误以为多跳没用。

### 先校准 `top_k`，再看数字

这是我实际踩到的坑，也是照着做时最容易犯的错，值得单独一节。

第一版默认 `top_k=10`，结果三档**全部 100%**，每一格满分。数字很漂亮，但没有意义 ——
语料只有 5 篇文档，取 10 个 chunk 等于每次把整个语料库都交给模型
（实测每题召回 4~5 篇 / 共 5 篇），检索策略再差也不会漏。
我测的不是检索质量，是「库里有没有这份文档」。

默认值改成 3 之后指标才开始有区分度。

**所以换上自己的语料后，先确认指标能分辨出差别。** 如果三档结果一样，
第一嫌疑不是「三档没差别」，而是 `top_k` 相对语料规模太大。
先把它调小到能看出差距，再去解读绝对值。

局部重跑很方便：

```bash
./eval.sh --only M1,M4              # 只看某几题
./eval.sh --modes rerank,agentic    # 只跑某几档
./eval.sh --top-k 5 --json out.json # 换 top_k 并导出原始数据
```

## 8. 清理

```bash
./cleanup.sh
```

先清空 S3 桶再删栈（桶里有对象时 CloudFormation 删不掉桶），删完不留任何资源。
演示完记得跑，虽然这个 demo 闲置几乎不花钱。

---

# 第二部分：数据与推荐配置

## 9. 实测结果

`--top-k 3`，us-west-2，跑三次。三档的区别只有第 6 节那两个参数：

| 档位 | 文档召回 | 证据里的事实覆盖 | 答案里的事实覆盖 | 中位耗时 |
|---|---|---|---|---|
| 纯向量（重排 `NONE`） | 72.2% | 78.7% | — | 0.7s |
| 托管重排（`MANAGED`） | 77.8% | 89.8% | — | 0.7s |
| 多跳（`AgenticRetrieveStream`） | **88.9%** | **96.3%** | 89.8% / 92.6% / 89.8% | 8.5~9.5s |

三次跑**前三列完全一致**，逐题明细也一致，只有生成的答案事实覆盖在
89.8% 和 92.6% 之间波动。检索部分稳得可以直接引用，生成部分不行。

按题型拆开，差距的来源就清楚了：

| 题型 | 纯向量 | 托管重排 | 多跳 |
|---|---|---|---|
| 单跳（4 题） | 100% | 100% | 100% |
| 多跳（5 题） | 50% | 60% | **80%** |

两道库外问题，多跳档三次都正确拒答，没有编造。

## 10. 推荐配置

### 托管重排：直接打开

中位耗时 0.7s 对 0.7s，证据事实覆盖 78.7% → 89.8%。
一个枚举值换来的，没有额外资源、没有额外配置、不用申请模型访问。

**如果你只改一个参数，改这个。** 这也是为什么第 1 节要强调：
自定义嵌入模型会让托管重排不可用 —— 除非你有明确理由必须自带嵌入模型，
否则用默认的托管嵌入，把这一档留着。

### 多跳：按需用，不要全量套

单跳题三档都是 100%，而多跳慢十几倍。把所有查询都塞给 `AgenticRetrieveStream`
是在为不需要的能力付钱。

务实的做法是分流：常规查询走 `Retrieve` + 托管重排，
识别为复杂/跨文档的查询才走多跳。

### 好消息：多跳的开销本身是自适应的

这一点是实测里最让我意外的。把 trace 事件打出来（第三次跑的输出）：

```
S1（单跳）   SpeculativeRetrieval x2, Planning x2
S2（单跳）   SpeculativeRetrieval x2, Planning x2
S3（单跳）   SpeculativeRetrieval x2, Planning x2
S4（单跳）   SpeculativeRetrieval x2, Planning x2
M1（多跳）   SpeculativeRetrieval x2, Planning x4, Retrieval x4
M2（多跳）   SpeculativeRetrieval x2, Planning x6, Retrieval x8
M3（多跳）   SpeculativeRetrieval x2, Planning x2
M4（多跳）   SpeculativeRetrieval x2, Planning x4, Retrieval x4
M5（多跳）   SpeculativeRetrieval x2, Planning x2
```

每题都有的 `SpeculativeRetrieval` 就是第 4 节那个「规划前先检索一次以降低延迟」的步骤。
关键在后面：**只有真正需要跨文档拼答案的 M1、M2、M4 额外触发了规划和检索轮次，
单跳题一轮都没多花。** 这正是文档里说的「模型评估结果够不够，不够才加轮次」。

这改变了成本估算的算法：不是「所有查询 × 多跳单价」，而是
「需要多跳的那部分查询才付多跳的钱」。

不过这里我一开始下了个过头的结论。前两次跑的步骤序列一模一样，我以为它是确定性的；
第三次跑 M2 就从 `Planning x4, Retrieval x4` 变成了 `x6 / x8`。
准确的说法是：**哪几题会升级检索，三次完全一致；升级几轮，不保证。**
做容量和费用估算时，这个比例要从你自己的真实查询分布里取，并留出余量。

顺带一提，`FullDocumentExpansion` 这 11 道题一次都没触发。
按文档的说法它要在模型认为需要整篇文档时才出现（摘要、确认完整性这类），
我的题目都是精确事实查找，不需要全文，所以合理。

### 两个指标都要看

两个没解决的 case 正好说明这一点：

**M3**（信号 -100 dBm 能不能 OTA、要不要加装天线）三档都没召回错误码手册。
但关键数字 `-105 dBm` 仍被覆盖到了 —— OTA 文档里有一句提醒专门对比了这两个阈值。
文档召回扣分，事实覆盖满分，实际上这题能答对。

**M4**（西北 8000 台批量升固件）三档都漏了 OTA 文档里「首批 1%」这条灰度要求。
合规文档的三个 chunk 抢满了名额。这个是真漏了。

**只看一个指标会得出相反结论**：光看文档召回会低估 M3，光看事实覆盖会漏掉 M4。
所以第 7 节的 ground truth 格式里两样都要填。

## 11. 配额与费用

官方给出的托管知识库配额里，几个和容量规划直接相关的
（[来源](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-quotas.html)）：

| 配额 | 默认值 | 可调 |
|---|---|---|
| 每 region 每账号托管 KB 数 | 10,000 | 是 |
| 每个 KB 的数据源数 | 200 | 否 |
| 每个 KB 的原始数据存储 | 10 TB | 否 |
| 单次 `Retrieve` / `AgenticRetrieveStream` 查询输入字符数（英文） | 10,000 | 否 |
| 每个 KB 的 `Retrieve` RPM | 600（支持突发 25 RPS） | 是 |
| 每账号 `AgenticRetrieveStream` RPM | 300 | 是 |

注意最后两行的差别：**普通检索的配额按知识库算，多跳按账号算，而且低一个量级。**
如果你打算全量走多跳，先照这个数算一遍并申请提额 —— 这也是上面建议分流的另一个理由。

费用方面，最重要的一点是**闲置接近 $0，没有任何按小时计费的常驻资源**。
这是托管知识库和自建 OpenSearch Serverless 最大的账面区别，后者仅集合本身
就约 $0.24/OCU-小时起，不查也在计费。

跑一次完整评测（11 题 × 3 档）的量级是几十次检索加十来次多跳调用，成本在 $1 以内。

## 12. IaC：模板可以纯原生

`AWS::Bedrock::KnowledgeBase`（`Type: MANAGED`）和 `AWS::Bedrock::DataSource`
（`Type: MANAGED_KNOWLEDGE_BASE_CONNECTOR`）都是原生 CloudFormation 资源，
不需要包 Lambda 自定义资源。

整个 stack 是一个 S3 桶、一个 IAM 角色、一个知识库、一个数据源 ——
没有 Lambda、没有自定义资源、没有资产、不需要暂存桶。
这让模板可以完全自包含地分发，客户不用先准备任何东西。

数据源那里有个小陷阱：`ConnectorParameters` 里所有值必须是字符串。
CloudFormation 的 `Json` 属性会把数字变成字符串，服务端随后报类型错误，
所以模板里 `version: "1"` 的引号是必须的。

```yaml
CorpusDataSource:
  Type: AWS::Bedrock::DataSource
  Properties:
    Name: corpus
    KnowledgeBaseId: !GetAtt KnowledgeBase.KnowledgeBaseId
    DataDeletionPolicy: DELETE
    DataSourceConfiguration:
      Type: MANAGED_KNOWLEDGE_BASE_CONNECTOR
      ManagedKnowledgeBaseConnectorConfiguration:
        ConnectorParameters:
          type: S3
          version: "1"          # 引号必须有
          connectionConfiguration:
            bucketName: !Ref CorpusBucket
            bucketOwnerAccountId: !Ref AWS::AccountId
          filterConfiguration:
            inclusionPrefixes:
              - docs/
```

## 13. SDK 版本

托管知识库和 `AgenticRetrieveStream` 是较新的服务模型更新，
CloudShell 自带的 boto3 可能还不认识（我本机 1.43.25 就不认，1.43.89 可以）。

**做能力探测，不要比版本号**：

```python
import botocore.session
session = botocore.session.get_session()
kb_config = (session.get_service_model("bedrock-agent")
             .operation_model("CreateKnowledgeBase")
             .input_shape.members["knowledgeBaseConfiguration"].members)
assert "MANAGED" in kb_config["type"].enum
assert "AgenticRetrieveStream" in session.get_service_model(
    "bedrock-agent-runtime").operation_names
```

走 botocore 的 loader 而不是建 client，这样不依赖 region 和凭证。
demo 里的脚本检测到版本不够会自建一个临时 venv 装新版再重新拉起自己，
所以你在 CloudShell 里不用管这件事。

## 14. 这些数字的适用范围

11 道题、5 篇文档，样本量不足以支撑「A 比 B 好 X%」这种结论。

不过偏差的方向是可判断的：**小语料对纯向量检索是有利的**（文档少、混淆项少）。
真实语料上纯向量那一档大概率比 72.2% 更差，三档差距会更大而不是更小。
换句话说，托管重排和多跳的收益在真实规模上应该比这张表更明显。

另外还有几处我没测：并发表现、千篇量级语料、多语言混合语料、
文档级访问控制（`userContext.userId` 那条路径）、以及把 AgentCore Memory
接进多跳检索的效果。做方案前建议先自己验。

有个小观察供参考：`metadata._language_code` 把我这 5 篇中文文档全判成了 `en`。
检索分数正常（相关 chunk 0.99+），没看到实际影响，
但如果你打算按语言做元数据过滤，先验证这个字段而不是直接信它。

## 15. 三条结论

1. **做 RAG 从托管知识库起步，并且用默认的托管嵌入。** 不用选型、不用申请模型访问、
   没有按小时计费的资源，而且能把托管重排这一档留着。
2. **托管重排直接开。** 一个枚举值，实测证据事实覆盖 78.7% → 89.8%，
   延迟基本不变。这是全篇性价比最高的改动。
3. **多跳按需分流，别全量套。** 单跳题上它没有优势且慢十几倍，配额还按账号算、
   只有 300 RPM。好在服务自己会判断要不要加检索轮次，真多跳的题才多花钱。

还有一条方法上的：**别直接采信任何人的通用跑分，包括这篇。**
先在自己的语料上确认指标有区分度，再看数字 —— 我第一次跑出来的满分表就是反例。
而且要多跑几次：我在两次一致的 trace 上得出的「确定性」结论，第三次就被推翻了。

---

## 参考资料

- [Create a managed knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-create.html) —— 嵌入模型选项、支持的连接器、`managedSearchConfiguration` 提示
- [Use agentic retrieval to query a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html) —— 多跳的工作流程、trace 事件定义、IAM 权限、注意事项
- [Retrieving information from data sources](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-how-retrieval.html) —— 四个检索 API 的分工
- [Service quotas for managed knowledge bases](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-quotas.html)
- [AgenticRetrieveStream API 参考](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_AgenticRetrieveStream.html)
- [Connect to your knowledge base through AgentCore Gateway](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-gateway-target.html) —— 把知识库作为 MCP 工具暴露给 Agent

上述 AWS 文档内容经改写以符合授权要求；实测数据与结论为本文作者在 us-west-2 自行运行所得。
